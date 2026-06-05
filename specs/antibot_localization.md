# Локализация антибот-страниц

eBay (как и большинство крупных сайтов) отдаёт страницу мягкой блокировки
«Pardon Our Interruption» на языке прокси-IP. На практике мы используем
**американские прокси**, поэтому по умолчанию ловим только английский
вариант. Остальные локали — на случай, если в пул попадёт прокси из другого
региона.

---

## Детекция

Проверяем `<title>` против списка известных шаблонов:

```python
ANTIBOT_PARDON_PATTERNS = (
    "<title>Pardon Our Interruption",          # en — основной
    "<title>Desculpe interromper",             # pt-BR
    "<title>Disculpe la interrupción",         # es
    "<title>Désolé pour l'interruption",       # fr
    "<title>Entschuldigen Sie die Störung",    # de
)

def is_pardon(html: str) -> bool:
    return any(p in html for p in ANTIBOT_PARDON_PATTERNS)
```

Pardon надёжнее всего виден ещё раньше title — по **URL**: eBay редиректит
запрос на `/splashui/challenge?...&ru=<исходный url>` (подтверждено live).

С `Access Denied` похожая история, но реже — обычно строка остаётся
английской на всех языках:

```python
ANTIBOT_AD_PATTERNS = (
    "<title>Access Denied",
    "<TITLE>Access Denied",   # встречается в верхнем регистре
)
```

Третий блок — транзиентный **Error Page** (`<title>Error Page | eBay`,
«SORRY — Something went wrong», без JS-челленджа); типичен при холодном
заходе на SRP без прогрева главной.

Сам Pardon — JS-челлендж, проходит сам через несколько секунд (или после
капчи в видимом браузере). Политики обработки всех трёх блоков и ожидание
готовности страницы — [page_readiness.md](page_readiness.md).
