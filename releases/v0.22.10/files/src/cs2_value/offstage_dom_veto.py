from __future__ import annotations

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
) -> tuple[VetoAction, ...]:
    """Convert map-tab/logo DOM evidence into ordered picks and a decider.

    Live Offstage scoreboards may render the picker only as a team logo next to
    the map tab, so that owner is absent from ``inner_text()``. We only emit a
    DOM-derived veto when every non-decider map is tied unambiguously to one of
    the two team-logo asset signatures. This deliberately prefers no result over
    guessing from score, map order, or the generic green Pick badge.
    """
    team_assets = payload.get("team_assets") or {}
    assets_a = set(team_assets.get("a") or ())
    assets_b = set(team_assets.get("b") or ())
    if not assets_a or not assets_b:
        return ()

    best: dict[str, dict] = {}
    for item in payload.get("maps") or ():
        raw_name = str(item.get("map") or "").strip()
        canonical = next((name for name in KNOWN_MAPS if name.lower() == raw_name.lower()), None)
        if canonical is None:
            continue
        score = int(item.get("score") or 0)
        current = best.get(canonical.lower())
        if current is None or score > int(current.get("score") or 0):
            candidate = dict(item)
            candidate["map"] = canonical
            best[canonical.lower()] = candidate

    maps = sorted(best.values(), key=lambda item: int(item.get("order") or 0))
    expected_maps = best_of if best_of in {3, 5} else None
    if expected_maps is None or len(maps) != expected_maps:
        return ()

    picked: list[tuple[str, str | None]] = []
    identified = 0
    for item in maps:
        assets = set(item.get("assets") or ())
        has_a = bool(assets & assets_a)
        has_b = bool(assets & assets_b)
        owner = None
        if has_a and not has_b:
            owner = team_a
            identified += 1
        elif has_b and not has_a:
            owner = team_b
            identified += 1
        picked.append((str(item["map"]), owner))

    # BO3: two team picks + one ownerless decider. BO5 follows the same rule.
    if identified < expected_maps - 1:
        return ()
    unresolved = [idx for idx, (_, owner) in enumerate(picked) if owner is None]
    if len(unresolved) != 1:
        return ()

    decider_idx = unresolved[0]
    return tuple(
        VetoAction(
            map_name=map_name,
            action="decider" if idx == decider_idx else "pick",
            team_name=None if idx == decider_idx else owner,
            action_order=idx + 1,
            series_map_order=idx + 1,
        )
        for idx, (map_name, owner) in enumerate(picked)
    )


def extract_veto_from_scoreboard_dom(
    page,
    team_a: str,
    team_b: str,
    best_of: int | None,
) -> tuple[VetoAction, ...]:
    """Read team-logo ownership from the rendered Offstage map tabs."""
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
  const maps = [];
  const all = Array.from(document.querySelectorAll('body *'));
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
    maps,
  };
}
""",
        {"teamA": team_a, "teamB": team_b, "knownMaps": list(KNOWN_MAPS)},
    )
    if not isinstance(payload, dict):
        return ()
    return veto_from_dom_payload(payload, team_a, team_b, best_of)
