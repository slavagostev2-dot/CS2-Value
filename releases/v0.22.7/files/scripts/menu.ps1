$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Cli = Join-Path $Root ".venv\Scripts\cs2-value.exe"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Cli)) {
    Write-Host "CS2 Value не установлен. Запусти INSTALL.bat." -ForegroundColor Red
    Read-Host "Нажми Enter"
    exit 1
}
$Version = (& $Cli version | Select-Object -First 1)
while ($true) {
    Clear-Host
    Write-Host "=========================================="
    Write-Host "             CS2 Value v$Version" -ForegroundColor Cyan
    Write-Host "=========================================="
    Write-Host ""
    Write-Host " 1. Проанализировать ссылку Fonbet"
    Write-Host " 2. Проверить подключение к Fonbet"
    Write-Host " 3. Собрать до 100 старых матчей Offstage"
    Write-Host " 4. Собрать до 500 старых матчей Offstage"
    Write-Host " 5. Углубить историю до 3000 матчей Offstage"
    Write-Host " 6. Показать состояние базы (просто)"
    Write-Host " 7. Подробно проверить качество базы"
    Write-Host " 8. Проверить первую модель на истории"
    Write-Host " 9. Диагностика: почему модель пока слабая"
    Write-Host "10. Проверить калибровку и сохранить Platt-прогнозы"
    Write-Host "11. Настроить / проверить API-ключ OddsPapi"
    Write-Host "12. Загрузить исторические коэффициенты Pinnacle (сначала тест 50)"
    Write-Host "13. Проверить VALUE на уже загруженных коэффициентах"
    Write-Host "14. MODEL v2: проверить добавление K/D"
    Write-Host "15. MODEL v2: проверить Map Pool V1"
    Write-Host "16. VETO/PICK V1: проверить текущий Fonbet матч"
    Write-Host "17. Запустить автоматические тесты"
    Write-Host "18. VETO/PICK V1: автоматически следить до старта"
    Write-Host " 0. Обновить CS2 Value через GitHub" -ForegroundColor Green
    Write-Host " X. Выход"
    Write-Host ""
    $Choice = Read-Host "Выбери пункт"
    switch ($Choice) {
        "1" {
            $Url = Read-Host "Вставь ссылку Fonbet"
            if ($Url) { & $Cli fonbet-analyze $Url }
            Read-Host "Нажми Enter"
        }
        "2" {
            $Url = Read-Host "Вставь ссылку Fonbet"
            if ($Url) { & $Cli fonbet-diagnose $Url }
            Read-Host "Нажми Enter"
        }
        "3" {
            Clear-Host
            Write-Host "Сбор 100 матчей запущен." -ForegroundColor Cyan
            Write-Host "Не закрывай это окно. Теперь прогресс будет показываться ниже." -ForegroundColor Yellow
            Write-Host ""
            & $Cli collect-finished --limit 100 --delay 1
            if ($LASTEXITCODE -eq 0) {
                Write-Host ""
                Write-Host "Сбор завершён. Текущее состояние базы:" -ForegroundColor Green
                & $Cli status
            } else {
                Write-Host ""
                Write-Host "Сбор закончился ошибкой. Последние сообщения видны выше." -ForegroundColor Red
            }
            Write-Host ""
            Write-Host "Журнал: $Root\logs\collect_latest.log"
            Read-Host "Нажми Enter"
        }
        "4" {
            Clear-Host
            Write-Host "Сбор до 500 матчей запущен." -ForegroundColor Cyan
            Write-Host "Не закрывай это окно. Прогресс будет показываться ниже." -ForegroundColor Yellow
            Write-Host ""
            & $Cli collect-finished --limit 500 --delay 1
            if ($LASTEXITCODE -eq 0) {
                Write-Host ""
                Write-Host "Сбор завершён. Текущее состояние базы:" -ForegroundColor Green
                & $Cli status
            } else {
                Write-Host ""
                Write-Host "Сбор закончился ошибкой. Последние сообщения видны выше." -ForegroundColor Red
            }
            Write-Host ""
            Write-Host "Журнал: $Root\logs\collect_latest.log"
            Read-Host "Нажми Enter"
        }
        "5" {
            Clear-Host
            Write-Host "Глубокий сбор до 3000 матчей запущен." -ForegroundColor Cyan
            Write-Host "Это может занять больше часа. Уже сохранённые матчи повторно скачиваться не будут." -ForegroundColor Yellow
            Write-Host "Окно можно остановить Ctrl+C; уже сохранённые матчи останутся в базе, а следующий запуск продолжит сбор." -ForegroundColor Yellow
            Write-Host ""
            & $Cli collect-finished --limit 3000 --delay 1
            if ($LASTEXITCODE -eq 0) {
                Write-Host ""
                Write-Host "Глубокий сбор завершён. Текущее состояние базы:" -ForegroundColor Green
                & $Cli status
            } else {
                Write-Host ""
                Write-Host "Сбор был остановлен или завершился ошибкой. Уже сохранённые матчи не потеряны." -ForegroundColor Yellow
            }
            Write-Host ""
            Write-Host "Журнал: $Root\logs\collect_latest.log"
            Read-Host "Нажми Enter"
        }
        "6" { & $Cli status; Read-Host "Нажми Enter" }
        "7" { & $Cli audit; Read-Host "Нажми Enter" }
        "8" { & $Cli model-report --min-train 100 --retrain-every 25; Read-Host "Нажми Enter" }
        "9" { & $Cli model-diagnose --min-train 100 --retrain-every 25; Read-Host "Нажми Enter" }
        "10" {
            Clear-Host
            Write-Host "Честная walk-forward проверка калибровки запущена." -ForegroundColor Cyan
            Write-Host "Если Platt снова улучшит Brier и Log Loss, его out-of-sample прогнозы сохранятся для будущего теста коэффициентов." -ForegroundColor Yellow
            Write-Host ""
            & $Cli calibration-report --model-min-train 100 --model-retrain-every 25 --calibration-min-train 250 --calibration-retrain-every 50 --save-platt --model-version backtest-logit-platt-v0.20
            Read-Host "Нажми Enter"
        }
        "11" {
            Clear-Host
            Write-Host "Сейчас программа попросит бесплатный API-ключ OddsPapi и проверит его." -ForegroundColor Cyan
            Write-Host "Ключ сохранится только локально рядом с базой данных." -ForegroundColor Yellow
            & $Cli oddspapi-setup
            Read-Host "Нажми Enter"
        }
        "12" {
            Clear-Host
            Write-Host "Исторические коэффициенты Pinnacle." -ForegroundColor Cyan
            Write-Host "Сначала матчи сопоставятся пакетно, затем historical-odds будут загружаться примерно по одному запросу в 5 секунд." -ForegroundColor Yellow
            Write-Host "Для первой реальной проверки рекомендуется 50 матчей." -ForegroundColor Yellow
            $LimitText = Read-Host "Сколько новых матчей загрузить? [50]"
            if (-not $LimitText) { $LimitText = "50" }
            $Limit = 0
            if (-not [int]::TryParse($LimitText, [ref]$Limit) -or $Limit -lt 1 -or $Limit -gt 500) {
                Write-Host "Нужно ввести число от 1 до 500." -ForegroundColor Red
                Read-Host "Нажми Enter"
                continue
            }
            Write-Host ""
            & $Cli oddspapi-history --model-version backtest-logit-platt-v0.20 --bookmaker pinnacle --limit $Limit
            Read-Host "Нажми Enter"
        }
        "13" {
            Clear-Host
            & $Cli value-report --model-version backtest-logit-platt-v0.20 --bookmaker pinnacle
            Read-Host "Нажми Enter"
        }
        "14" {
            Clear-Host
            Write-Host "MODEL v2 / шаг 1: сравниваем старую модель с той же моделью + исторический K/D." -ForegroundColor Cyan
            Write-Host "Текущий матч не подглядывается; сравнение идёт на одинаковом walk-forward окне и после Platt." -ForegroundColor Yellow
            Write-Host "ROI на этом шаге специально не используется для выбора признака." -ForegroundColor Yellow
            Write-Host ""
            & $Cli model-v2-kd-report --model-min-train 100 --model-retrain-every 25 --calibration-min-train 250 --calibration-retrain-every 50
            Read-Host "Нажми Enter"
        }
        "15" {
            Clear-Host
            Write-Host "MODEL v2 / шаг 2: проверяем исторический Map Pool V1 отдельно и поверх кандидата K/D." -ForegroundColor Cyan
            Write-Host "Текущий veto/picks не используются; только карты из завершённой прошлой истории." -ForegroundColor Yellow
            Write-Host "ROI снова не используется для выбора признака." -ForegroundColor Yellow
            Write-Host ""
            & $Cli model-v2-map-report --model-min-train 100 --model-retrain-every 25 --calibration-min-train 250 --calibration-retrain-every 50
            Read-Host "Нажми Enter"
        }
        "16" {
            Clear-Host
            Write-Host "VETO/PICK V1: проверяем, появились ли реальные пики/баны на текущем матче." -ForegroundColor Cyan
            Write-Host "Снимок сохраняется с точным временем. Пока он НЕ меняет вероятность — сначала валидируем сбор." -ForegroundColor Yellow
            $Url = Read-Host "Вставь ссылку Fonbet"
            if ($Url) { & $Cli veto-probe-fonbet $Url }
            Read-Host "Нажми Enter"
        }
        "17" { & $Py -m pytest -q; Read-Host "Нажми Enter" }
        "18" {
            Clear-Host
            Write-Host "VETO/PICK V1: автоматическое наблюдение за реальными пиками/банами." -ForegroundColor Cyan
            Write-Host "Чем дальше матч, тем реже проверки; ближе к старту частота автоматически увеличится." -ForegroundColor Yellow
            Write-Host "Одинаковое veto в базу повторно не сохраняется. Остановить можно Ctrl+C." -ForegroundColor Yellow
            $Url = Read-Host "Вставь ссылку Fonbet"
            if ($Url) { & $Cli veto-watch-fonbet $Url --interval-seconds 120 --stop-after-start-minutes 15 }
            Read-Host "Нажми Enter"
        }
        "0" {
            Clear-Host
            Write-Host "Проверяем GitHub и устанавливаем доступные обновления." -ForegroundColor Cyan
            Write-Host "База, API-ключи и логи сохраняются. При ошибке изменённые файлы откатываются." -ForegroundColor Yellow
            Write-Host ""
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\github_update.ps1")
            $UpdateCode = $LASTEXITCODE
            if ($UpdateCode -eq 10) {
                Write-Host ""
                Write-Host "Обновление установлено. Перезапускаю CS2 Value..." -ForegroundColor Green
                Start-Sleep -Seconds 1
                Start-Process -FilePath (Join-Path $Root "START.bat") -WorkingDirectory $Root
                exit 0
            }
            Read-Host "Нажми Enter"
        }
        { $_ -eq "x" -or $_ -eq "X" } { exit 0 }
    }
}
