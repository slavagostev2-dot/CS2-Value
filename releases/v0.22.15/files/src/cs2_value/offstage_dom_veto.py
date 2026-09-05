from __future__ import annotations

from dataclasses import replace
from hashlib import sha1

from .models import VetoAction


KNOWN_MAPS = (
    "Ancient", "Anubis", "Cache", "Dust2", "Inferno",
    "Mirage", "Nuke", "Overpass", "Train", "Vertigo",
)


def veto_from_dom_payload(
    payload: dict,
    team_a: str,
    team_b: str,
    best_of: int | None,
    *,
    text_veto: tuple[VetoAction, ...] = (),
) -> tuple[VetoAction, ...]:
    """Convert evidence from the rendered Pick badges into ordered map picks.

    The large logo inside a completed map card can be the *map winner*, not the
    team that picked the map.  Ownership is therefore read only from the small
    ``Pick`` badge itself.  The visible-text parser supplies the ordered series
    structure (PICK/PICK/DECIDER); DOM evidence supplies picker identity.

    For a standard BO3, if exactly one Pick badge is tied directly to a team and
    the other Pick badge is present but rendered with an unmatched SVG, the second
    pick belongs to the opposite team.  This complement is deliberately disabled
    for BO5 and when the text series structure is incomplete.
    """
    team_assets = payload.get("team_assets") or {}
    assets_a = set(team_assets.get("a") or ())
    assets_b = set(team_assets.get("b") or ())
    if not assets_a or not assets_b:
        return ()

    series_text = sorted(
        (
            action for action in text_veto
            if action.action in {"pick", "decider"}
            and action.series_map_order is not None
        ),
        key=lambda action: int(action.series_map_order or 0),
    )
    expected_maps = best_of if best_of in {3, 5} else None
    if expected_maps is None or len(series_text) != expected_maps:
        return ()
    if [action.series_map_order for action in series_text] != list(range(1, expected_maps + 1)):
        return ()

    pick_rows = [action for action in series_text if action.action == "pick"]
    decider_rows = [action for action in series_text if action.action == "decider"]
    if best_of == 3 and (len(pick_rows) != 2 or len(decider_rows) != 1):
        return ()

    # Keep only badge evidence for maps that the text parser already identified as picks.
    badge_by_map: dict[str, dict] = {}
    for item in payload.get("pick_badges") or ():
        raw_name = str(item.get("map") or "").strip()
        canonical = next((name for name in KNOWN_MAPS if name.casefold() == raw_name.casefold()), None)
        if canonical is None:
            continue
        key = canonical.casefold()
        candidate = dict(item)
        candidate["map"] = canonical
        current = badge_by_map.get(key)
        # Prefer the smallest/closest badge root if JS produced duplicates.
        score = int(candidate.get("score") or 0)
        if current is None or score > int(current.get("score") or 0):
            badge_by_map[key] = candidate

    resolved: dict[str, str | None] = {}
    badge_present: set[str] = set()
    for action in pick_rows:
        key = action.map_name.casefold()
        item = badge_by_map.get(key)
        if item is None:
            resolved[key] = None
            continue
        badge_present.add(key)
        badge_assets = set(item.get("assets") or ())
        has_a = bool(badge_assets & assets_a)
        has_b = bool(badge_assets & assets_b)
        if has_a and not has_b:
            resolved[key] = team_a
        elif has_b and not has_a:
            resolved[key] = team_b
        else:
            resolved[key] = None

    # Safe BO3 complement: both Pick badges must exist, and exactly one owner must
    # be identified directly from the badge.  This never uses the large winner logo.
    if best_of == 3 and len(badge_present) == 2:
        owners = [resolved.get(action.map_name.casefold()) for action in pick_rows]
        if owners.count(team_a) == 1 and owners.count(team_b) == 0 and owners.count(None) == 1:
            owners[owners.index(None)] = team_b
        elif owners.count(team_b) == 1 and owners.count(team_a) == 0 and owners.count(None) == 1:
            owners[owners.index(None)] = team_a
        for action, owner in zip(pick_rows, owners):
            resolved[action.map_name.casefold()] = owner

    # Every pick must now have an owner; decider is intentionally ownerless.
    if any(resolved.get(action.map_name.casefold()) not in {team_a, team_b} for action in pick_rows):
        return ()
    if len({resolved[action.map_name.casefold()] for action in pick_rows}) < min(2, len(pick_rows)):
        return ()

    return tuple(
        VetoAction(
            map_name=action.map_name,
            action=action.action,
            team_name=(
                None if action.action == "decider"
                else resolved.get(action.map_name.casefold())
            ),
            action_order=index + 1,
            series_map_order=index + 1,
        )
        for index, action in enumerate(series_text)
    )


def merge_dom_veto_with_text(
    dom_veto: tuple[VetoAction, ...],
    text_veto: tuple[VetoAction, ...],
) -> tuple[VetoAction, ...]:
    """Prefer DOM evidence for map picks/owners and retain text-only bans.

    Offstage's rendered scoreboard can expose the picker only as a team logo, while
    the visible text parser can still reconstruct the full seven-map pool.  When both
    are available, DOM is authoritative for ordered PICK/DECIDER rows; text contributes
    only BAN rows that are not part of the played series.  The resulting action_order
    is a storage/display order (series maps first, excluded maps after), not a claim
    about the historical chronological ban sequence.
    """
    if not dom_veto:
        return tuple(text_veto)

    series_maps = {action.map_name.casefold() for action in dom_veto}
    bans = [
        action for action in text_veto
        if action.action == "ban" and action.map_name.casefold() not in series_maps
    ]
    merged = list(dom_veto)
    for action in bans:
        merged.append(
            replace(
                action,
                action_order=len(merged) + 1,
                series_map_order=None,
            )
        )
    return tuple(merged)


def extract_veto_from_scoreboard_dom(
    page,
    team_a: str,
    team_b: str,
    best_of: int | None,
    *,
    text_veto: tuple[VetoAction, ...] = (),
    debug=None,
) -> tuple[VetoAction, ...]:
    """Read team-logo ownership from the rendered Offstage Pick badges."""
    payload = page.evaluate(
        r"""
({teamA, teamB, knownMaps}) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const directText = el => Array.from(el.childNodes || [])
    .filter(n => n.nodeType === Node.TEXT_NODE)
    .map(n => n.textContent || '').join(' ').replace(/\s+/g, ' ').trim();
  const visible = el => {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
  };
  const assets = root => {
    if (!root) return [];
    const out = [];
    for (const img of root.querySelectorAll('img')) {
      const src = img.currentSrc || img.src || img.getAttribute('src') || img.getAttribute('data-src');
      if (src) out.push('img:' + src.split('?')[0]);
    }
    for (const svg of root.querySelectorAll('svg')) {
      const use = svg.querySelector('use');
      const href = use && (use.getAttribute('href') || use.getAttribute('xlink:href'));
      if (href) out.push('use:' + href);
      const paths = Array.from(svg.querySelectorAll('path'))
        .map(p => p.getAttribute('d')).filter(Boolean).join('|');
      if (paths) out.push('svgpath:' + paths);
    }
    const nodes = [root, ...Array.from(root.querySelectorAll('*')).slice(0, 30)];
    for (const node of nodes) {
      const bg = getComputedStyle(node).backgroundImage || '';
      const m = bg.match(/url\(["']?(.*?)["']?\)/);
      if (m && m[1] && !m[1].startsWith('data:')) out.push('bg:' + m[1].split('?')[0]);
    }
    return Array.from(new Set(out));
  };
  const bestRootForExactText = text => {
    const target = norm(text);
    const els = Array.from(document.querySelectorAll('body *'))
      .filter(el => visible(el) && norm(directText(el)) === target);
    let best = null;
    for (const el of els) {
      let cur = el;
      for (let depth = 0; cur && depth < 6; depth++, cur = cur.parentElement) {
        const r = cur.getBoundingClientRect();
        const role = (cur.getAttribute('role') || '').toLowerCase();
        const interactive = ['button','a'].includes(cur.tagName.toLowerCase()) ||
          ['tab','button'].includes(role) || getComputedStyle(cur).cursor === 'pointer';
        const txt = (cur.innerText || '').replace(/\s+/g, ' ').trim();
        let score = 0;
        if (interactive) score += 100;
        if (r.width <= 240 && r.height <= 100) score += 35;
        if (txt.length <= 45) score += 20;
        if (assets(cur).length) score += 10;
        if (!best || score > best.score) best = {root: cur, score};
      }
    }
    return best;
  };
  const teamAssetSet = team => {
    const found = bestRootForExactText(team);
    if (!found) return [];
    let cur = found.root;
    let bestAssets = assets(cur);
    for (let i = 0; cur && i < 4; i++, cur = cur.parentElement) {
      const a = assets(cur);
      const txt = norm(cur.innerText || '');
      if (a.length && txt.includes(norm(team)) &&
          !(txt.includes(norm(teamA)) && txt.includes(norm(teamB)))) {
        bestAssets = a;
        break;
      }
    }
    return bestAssets;
  };
  const all = Array.from(document.querySelectorAll('body *'));

  // Read picker identity only from the small Pick badge. Do not use logos from
  // the surrounding map card: after a map finishes that larger logo can denote
  // the map winner rather than the team that selected it.
  const mapForElement = el => {
    let cur = el;
    for (let depth = 0; cur && depth < 9; depth++, cur = cur.parentElement) {
      const txt = norm(cur.innerText || '');
      const present = knownMaps.filter(map => txt.includes(norm(map)));
      if (present.length === 1) return present[0];
    }
    return null;
  };
  const bestPickBadgeRoot = el => {
    let cur = el;
    let best = null;
    for (let depth = 0; cur && depth < 4; depth++, cur = cur.parentElement) {
      const r = cur.getBoundingClientRect();
      const txt = norm(cur.innerText || '');
      const a = assets(cur);
      let score = 0;
      if (txt === 'pick') score += 40;
      else if (txt.includes('pick') && txt.length <= 18) score += 20;
      if (a.length) score += 100;
      if (r.width > 0 && r.width <= 110 && r.height > 0 && r.height <= 50) score += 35;
      if (!best || score > best.score) best = {root: cur, score};
    }
    return best;
  };
  const pickBadges = [];
  const pickTextEls = all.filter(el => {
    if (!visible(el)) return false;
    const d = norm(directText(el));
    const i = norm(el.innerText || '');
    return d === 'pick' || i === 'pick';
  });
  for (const el of pickTextEls) {
    const best = bestPickBadgeRoot(el);
    if (!best) continue;
    const map = mapForElement(best.root);
    if (!map) continue;
    pickBadges.push({
      map,
      score: best.score,
      order: all.indexOf(el),
      assets: assets(best.root),
    });
  }

  // Broad map-card evidence remains diagnostic only. It is never used to infer
  // picker ownership because those cards can contain winner/status artwork.
  const maps = [];
  for (const map of knownMaps) {
    const target = norm(map);
    const els = all.filter(el => visible(el) && norm(directText(el)) === target);
    for (const el of els) {
      let cur = el;
      let best = null;
      for (let depth = 0; cur && depth < 6; depth++, cur = cur.parentElement) {
        const r = cur.getBoundingClientRect();
        const role = (cur.getAttribute('role') || '').toLowerCase();
        const interactive = ['button','a'].includes(cur.tagName.toLowerCase()) ||
          ['tab','button'].includes(role) || getComputedStyle(cur).cursor === 'pointer';
        const txt = (cur.innerText || '').replace(/\s+/g, ' ').trim();
        let score = 0;
        if (interactive) score += 100;
        if (r.width <= 240 && r.height <= 100) score += 35;
        if (txt.length <= 45) score += 20;
        if (assets(cur).length) score += 10;
        if (!best || score > best.score) best = {root: cur, score};
      }
      if (best) maps.push({
        map,
        score: best.score,
        order: all.indexOf(el),
        assets: assets(best.root),
      });
    }
  }
  return {
    team_assets: {a: teamAssetSet(teamA), b: teamAssetSet(teamB)},
    pick_badges: pickBadges,
    maps,
  };
}
""",
        {"teamA": team_a, "teamB": team_b, "knownMaps": list(KNOWN_MAPS)},
    )
    if not isinstance(payload, dict):
        if debug:
            debug("DOM debug: payload не получен.")
        return ()
    result = veto_from_dom_payload(
        payload, team_a, team_b, best_of, text_veto=text_veto
    )
    if not result and debug:
        team_assets = payload.get("team_assets") or {}
        assets_a = set(team_assets.get("a") or ())
        assets_b = set(team_assets.get("b") or ())
        debug(f"DOM debug: {team_a} assets={len(assets_a)}, {team_b} assets={len(assets_b)}")
        best = {}
        for item in payload.get("pick_badges") or ():
            name = str(item.get("map") or "").strip()
            score = int(item.get("score") or 0)
            current = best.get(name.casefold())
            if current is None or score > int(current.get("score") or 0):
                best[name.casefold()] = item
        if not best:
            debug("  DOM Pick badges: не найдены.")
        for item in sorted(best.values(), key=lambda x: int(x.get("order") or 0)):
            badge_assets = set(item.get("assets") or ())
            samples = []
            for asset in list(badge_assets)[:2]:
                text = str(asset)
                if text.startswith("svgpath:"):
                    text = "svgpath:#" + sha1(text.encode("utf-8")).hexdigest()[:10]
                elif len(text) > 90:
                    text = text[:42] + "..." + text[-42:]
                samples.append(text)
            debug(
                f"  DOM Pick {item.get('map')}: assets={len(badge_assets)}, "
                f"A-intersection={len(badge_assets & assets_a)}, B-intersection={len(badge_assets & assets_b)}, "
                f"sample={', '.join(samples) if samples else '-'}"
            )
    return result
