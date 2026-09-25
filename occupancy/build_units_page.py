# -*- coding: utf-8 -*-
"""Версионируемая md-выжимка поюнитного отчёта -> html-страница артефакта.

Зачем отдельный файл: build_units_report.py даёт xlsx для заказчика и md для git,
а страница по постоянной ссылке (docs/research/.../units-artifact.json) —
третий носитель того же содержания. Рендерим md, а не пересчитываем данные:
у отчёта один источник правды, и страница не может разойтись с выжимкой.

Вызов: build_units_page.py [<units-YYYY-MM-DD.md>] [<out.html>]
Без аргументов — свежайшая выжимка и agent-runtime/.../units-artifact.html.
Публикация — инструментом Artifact агента с url из метафайла (новый артефакт
не заводить никогда, у руководителя одна постоянная ссылка).

Три рода строк (решение Р3 спеки) рендерятся тремя разными чипами, а «факт» и
«продано вперёд» — двумя разными цветами: честность формата должна быть видна
глазом, а не только читаться в сноске.
"""
import html, re, sys
from pathlib import Path

# Подписи регионов реестра целей (targets.json -> region). Новый регион —
# новая строка здесь; неизвестный регион печатается как есть, а не падает.
# «ru50» — топ-50 глэмпингов России по подписчикам в Instagram (поручение
# заказчика 07.09.2026), третий трек слежки рядом с Подмосковьем и Самарой.
REGION_LABELS = {
    'samara': 'Самарская обл.',
    'msk3h': 'Подмосковье ≤3 ч',
    'ru50': 'Россия, топ-50 Instagram',
}

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    root = Path(__file__).resolve().parents[1]   # витрина: <repo>/occupancy/<file>
    SRC = Path(argv[0]) if argv else sorted(root.glob('docs/research/2026-08-10-glamping-market/units-*.md'))[-1]
    OUT = Path(argv[1]) if len(argv) > 1 else root / 'agent-runtime' / 'research' / 'glamping' / 'occupancy' / 'units-artifact.html'

    def inline(s):
        s = html.escape(s, quote=False)
        s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
        s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'«([^»]+)»', r'«\1»', s)
        return s

    def cell(s, col_name):
        t = s.strip()
        if t in ('—', '', '-'):
            return '<td class="dash">—</td>'
        if t.startswith('Домик:'):
            return f'<td><span class="kind kind-home">домик</span> {inline(t[6:].strip())}</td>'
        if t.startswith('Тип:') and 'фонд не снят' in t:
            return f'<td><span class="kind kind-nofund">тип, фонд не снят</span> {inline(t[4:].split("—")[0].strip())}</td>'
        if t.startswith('Тип:'):
            m = re.search(r'продано (\d+) из (\d+)', t)
            tail = f' <span class="sold">продано {m.group(1)} из {m.group(2)}</span>' if m else ''
            return f'<td><span class="kind kind-type">тип</span> {inline(t[4:].split("—")[0].strip())}{tail}</td>'
        if t in REGION_LABELS:
            return f'<td><span class="region region-{t}">{REGION_LABELS[t]}</span></td>'
        if re.fullmatch(r'-?\d+(\.\d+)?%', t):
            cls = 'num'
            if col_name and ('факт' in col_name.lower() or 'занято' in col_name.lower()):
                cls += ' fact'
            elif col_name and ('вперёд' in col_name.lower() or 'продано' in col_name.lower()):
                cls += ' ahead'
            return f'<td class="{cls}">{t}</td>'
        if re.fullmatch(r'\d+( из \d+)?', t):
            return f'<td class="num">{t}</td>'
        if t.startswith('нет') or t.startswith('да'):
            return f'<td class="{"no" if t.startswith("нет") else "yes"}">{inline(t)}</td>'
        return f'<td>{inline(t)}</td>'

    def table(rows):
        hdr = [c.strip() for c in rows[0].strip('|').split('|')]
        body = rows[2:]
        out = ['<div class="tablewrap"><table><thead><tr>']
        out += [f'<th>{inline(h)}</th>' for h in hdr]
        out.append('</tr></thead><tbody>')
        for r in body:
            cells = [c for c in r.strip().strip('|').split('|')]
            if len(cells) != len(hdr):
                cells = (cells + [''] * len(hdr))[:len(hdr)]
            cls = ' class="median"' if cells and cells[0].strip().startswith('МЕДИАНА') else ''
            out.append(f'<tr{cls}>' + ''.join(cell(c, h) for c, h in zip(cells, hdr)) + '</tr>')
        out.append('</tbody></table></div>')
        return '\n'.join(out)

    lines = SRC.read_text(encoding='utf-8').splitlines()
    parts, i, title = [], 0, 'Глэмпинги по юнитам'
    sec_n = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith('# '):
            title_full = ln[2:].strip(); i += 1; continue
        if ln.startswith('## '):
            sec_n += 1
            parts.append(f'</section><section id="s{sec_n}"><h2>{inline(ln[3:].strip())}</h2>'); i += 1; continue
        if ln.startswith('### '):
            parts.append(f'<h3>{inline(ln[4:].strip())}</h3>'); i += 1; continue
        if ln.startswith('|'):
            j = i
            while j < len(lines) and lines[j].startswith('|'): j += 1
            parts.append(table(lines[i:j])); i = j; continue
        if ln.startswith('- '):
            j = i; items = []
            while j < len(lines) and lines[j].startswith('- '):
                items.append(f'<li>{inline(lines[j][2:].strip())}</li>'); j += 1
            parts.append('<ul>' + ''.join(items) + '</ul>'); i = j; continue
        if ln.strip():
            j = i; buf = []
            while j < len(lines) and lines[j].strip() and not lines[j].startswith(('#', '|', '- ')):
                buf.append(lines[j].strip()); j += 1
            parts.append(f'<p>{inline(" ".join(buf))}</p>'); i = j; continue
        i += 1
    body = '\n'.join(parts)
    # Именно startswith, а не lstrip: lstrip снимает ЛЮБОЙ символ набора и у первого
    # абзаца отъедал открывающую «<» — на странице оставался литерал «p>».
    if body.startswith('</section>'):
        body = body[len('</section>'):]

    # Сводные числа для верхней полосы — из первой таблицы «Три рода строк» и строки «Всего строк»
    txt = SRC.read_text(encoding='utf-8')
    def grab(rx, default='—'):
        m = re.search(rx, txt); return m.group(1) if m else default
    n_rows = grab(r'Всего строк: (\d+)'); n_obj = grab(r'Объектов: (\d+)')
    n_home = grab(r'\| домик[^|]*\|[^|]*\| (\d+) \|'); n_type = grab(r'\| тип с известным фондом[^|]*\|[^|]*\| (\d+) \|')
    n_nofund = grab(r'\| тип, у которого фонд не снят[^|]*\|[^|]*\| (\d+) \|')

    page = f'''<meta charset="utf-8">
    <title>Глэмпинги по юнитам</title>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=PT+Serif:ital,wght@0,400;0,700;1,400&family=PT+Sans:wght@400;700&family=PT+Mono&display=swap">
    <style>
    :root {{
      --bg:#F4F6F3; --surface:#FFFFFF; --ink:#1B2621; --muted:#5F6E68; --line:#D9E0DC;
      --accent:#2F6E52; --accent-soft:#E3EFE8; --ahead:#3B5F8A; --ahead-soft:#E4ECF5;
      --caution:#A8741C; --caution-soft:#F6ECD9; --home:#2F6E52; --type:#3B5F8A;
      --shadow:0 1px 0 rgba(27,38,33,.06);
    }}
    @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
      --bg:#121A16; --surface:#1A2420; --ink:#E4EAE6; --muted:#98A69F; --line:#2C3A33;
      --accent:#79C29C; --accent-soft:#1F3229; --ahead:#8FB3E0; --ahead-soft:#1E2B3B;
      --caution:#E0AB4E; --caution-soft:#3A2E17; --home:#79C29C; --type:#8FB3E0;
      --shadow:none;
    }} }}
    :root[data-theme="dark"] {{
      --bg:#121A16; --surface:#1A2420; --ink:#E4EAE6; --muted:#98A69F; --line:#2C3A33;
      --accent:#79C29C; --accent-soft:#1F3229; --ahead:#8FB3E0; --ahead-soft:#1E2B3B;
      --caution:#E0AB4E; --caution-soft:#3A2E17; --home:#79C29C; --type:#8FB3E0;
      --shadow:none;
    }}
    body {{ background:var(--bg); color:var(--ink); font:16px/1.55 "PT Sans", "Helvetica Neue", Arial, sans-serif; margin:0; }}
    main {{ max-width:1100px; margin:0 auto; padding:40px 24px 80px; }}
    header.top {{ max-width:72ch; }}
    h1 {{ font:700 40px/1.1 "PT Serif", Georgia, serif; margin:0 0 8px; text-wrap:balance; letter-spacing:-.01em; }}
    .eyebrow {{ font:400 13px/1 "PT Mono", monospace; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin-bottom:14px; }}
    .lede {{ font-size:18px; color:var(--muted); max-width:66ch; margin:0 0 28px; }}
    .strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:0 0 36px; }}
    .tile {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:14px 16px; box-shadow:var(--shadow); }}
    .tile b {{ display:block; font:700 30px/1 "PT Mono", monospace; font-variant-numeric:tabular-nums; margin-bottom:6px; }}
    .tile span {{ font-size:13px; color:var(--muted); }}
    .tile.home b {{ color:var(--home); }} .tile.type b {{ color:var(--type); }} .tile.nofund b {{ color:var(--caution); }}
    section {{ margin:44px 0 0; }}
    h2 {{ font:700 26px/1.2 "PT Serif", Georgia, serif; margin:0 0 14px; padding-top:18px; border-top:1px solid var(--line); text-wrap:balance; }}
    h3 {{ font:700 18px/1.3 "PT Serif", Georgia, serif; margin:26px 0 8px; }}
    p, ul {{ max-width:72ch; }}
    p {{ margin:0 0 12px; }}
    li {{ margin:4px 0; }}
    code {{ font:13px "PT Mono", monospace; background:var(--accent-soft); padding:1px 5px; border-radius:3px; }}
    .tablewrap {{ overflow-x:auto; margin:12px 0 20px; border:1px solid var(--line); border-radius:6px; background:var(--surface); }}
    table {{ border-collapse:collapse; width:100%; font-size:14px; }}
    th {{ text-align:left; font:700 12px/1.3 "PT Sans", sans-serif; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); padding:10px 12px; border-bottom:1px solid var(--line); background:var(--surface); position:sticky; top:0; white-space:nowrap; }}
    td {{ padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
    tr:last-child td {{ border-bottom:0; }}
    td.num {{ font:14px "PT Mono", monospace; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    td.fact {{ color:var(--accent); font-weight:700; }}
    td.ahead {{ color:var(--ahead); }}
    td.dash {{ color:var(--muted); text-align:center; }}
    td.no {{ color:var(--caution); }}
    tr.median td {{ background:var(--accent-soft); font-weight:700; }}
    .kind {{ display:inline-block; font:700 11px/1 "PT Sans", sans-serif; letter-spacing:.04em; text-transform:uppercase; padding:4px 7px; border-radius:3px; margin-right:6px; white-space:nowrap; vertical-align:middle; }}
    .kind-home {{ background:var(--accent-soft); color:var(--home); }}
    .kind-type {{ background:var(--ahead-soft); color:var(--type); }}
    .kind-nofund {{ background:var(--caution-soft); color:var(--caution); }}
    .sold {{ font:13px "PT Mono", monospace; color:var(--type); white-space:nowrap; }}
    .region {{ display:inline-block; font-size:12px; padding:3px 8px; border-radius:12px; border:1px solid var(--line); white-space:nowrap; }}
    .region-samara {{ border-color:var(--accent); color:var(--accent); }}
    .region-ru50 {{ border-style:dashed; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:10px 18px; font-size:14px; color:var(--muted); margin:0 0 8px; }}
    .legend .fact-dot::before {{ content:""; display:inline-block; width:10px; height:10px; border-radius:2px; background:var(--accent); margin-right:6px; }}
    .legend .ahead-dot::before {{ content:""; display:inline-block; width:10px; height:10px; border-radius:2px; background:var(--ahead); margin-right:6px; }}
    footer {{ margin-top:56px; padding-top:16px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; max-width:72ch; }}
    @media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
    </style>
    <main>
    <header class="top">
      <div class="eyebrow">Слежка за загрузкой · снимок 04.09.2026 · Самарская область и Подмосковье</div>
      <h1>Глэмпинги по юнитам</h1>
      <p class="lede">Одна строка — один домик или тип размещения, каким его продаёт модуль бронирования. Факт по прошедшим ночам и продажи вперёд — две разные цифры, они никогда не в одной колонке.</p>
    </header>
    <div class="strip">
      <div class="tile"><b>{n_obj}</b><span>объектов с цифрами</span></div>
      <div class="tile"><b>{n_rows}</b><span>строк-юнитов</span></div>
      <div class="tile home"><b>{n_home}</b><span>домиков поимённо — движок адресует физический номер</span></div>
      <div class="tile type"><b>{n_type}</b><span>типов с известным фондом — «продано N из M»</span></div>
      <div class="tile nofund"><b>{n_nofund}</b><span>типов, у которых фонд не снят — занижение</span></div>
    </div>
    <div class="legend"><span class="fact-dot">факт — реализованная занятость прошедших ночей</span><span class="ahead-dot">продано вперёд — глубина продаж на будущие ночи</span></div>
    <section id="s0">
    {body}
    </section>
    <footer>Источник — датированные снапшоты модулей бронирования (agent-runtime/research/glamping/occupancy/snapshots), ежедневный прогон в 06:30. Все проценты — оценка сверху: «занято» и «закрыто владельцем» в виджете неотличимы. Версионируемая выжимка: docs/research/2026-08-10-glamping-market/units-2026-09-04.md; таблица для заказчика — xlsx рядом со снапшотами.</footer>
    </main>
    '''
    OUT.write_text(page, encoding='utf-8')
    print(f'записано {OUT} ({OUT.stat().st_size//1024} КБ); тайлы: объектов={n_obj}, строк={n_rows}, домиков={n_home}, типов={n_type}, без фонда={n_nofund}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
