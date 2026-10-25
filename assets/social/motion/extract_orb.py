"""Extract the actual app artwork without importing Qt or changing the app."""
import ast
from pathlib import Path
import re

root = Path(__file__).resolve().parents[3]
source = ast.parse((root / 'src/ui/hud.py').read_text(encoding='utf-8'))
html = next(node.value.value for node in source.body if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'ANIMATION_HTML' for t in node.targets))
css = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
css = css[css.index('.loader {'):css.index('.preview-badge {')]
markup = html[html.index('  <div class="loader"'):html.index('  <div class="preview-badge"')]
css += '\n.loader { --size: 1; filter: hue-rotate(157deg); }\n'
target = Path(__file__).parent
(target / 'orb.css').write_text('/* Extracted from src/ui/hud.py. Original orb: Uiverse / andrew-manzyk. */\n' + css, encoding='utf-8')
(target / 'orb.html').write_text(markup, encoding='utf-8')
print('Extracted actual CSS/SVG orb to', target)
