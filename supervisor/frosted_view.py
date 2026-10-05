"""Shared frosted surfaces for the settings and image result pages."""

FROSTED_CSS = """
:root {
 --glass-panel:rgba(255,255,255,.66);
 --glass-field:rgba(255,255,255,.72);
 --glass-line:rgba(73,107,94,.17);
 --glass-shadow:0 12px 36px rgba(38,73,58,.06),inset 0 1px 0 rgba(255,255,255,.8);
}
body.dark {
 --glass-panel:rgba(31,42,37,.76);
 --glass-field:rgba(18,28,23,.8);
 --glass-line:rgba(190,216,203,.2);
 --glass-shadow:0 12px 36px rgba(0,0,0,.2),inset 0 1px 0 rgba(255,255,255,.06);
}
gradio-app, .gradio-container { background:transparent !important; }
.gradio-container {
 --block-background-fill:var(--glass-panel);
 --input-background-fill:var(--glass-field);
 --border-color-primary:var(--glass-line);
 --block-border-width:1px;
 --block-label-text-color:var(--body-text-color);
 --button-primary-background-fill:var(--page-accent);
 --button-primary-background-fill-hover:var(--page-accent);
 --button-primary-text-color:var(--page-on-accent,#fff);
}
.gradio-container > .main { width:100% !important; max-width:100% !important; padding:0 !important; }
.gradio-container .form { background:transparent !important; border-color:transparent !important; gap:12px; }
#studio-control-words .control-word-line .form { gap:8px !important; min-width:0 !important; }
#editor-page, #results-page { isolation:isolate; }
#editor-page .compact-title { background:transparent !important; border:0 !important; padding:0 !important; }
#editor-page .app-title { border:0; padding:0; }
#editor-page .app-title h1 { font-size:22px !important; font-weight:650; letter-spacing:-.4px; }
#editor-page > .tabs, #top-toolbar, #results-page > .results-nav {
 background:var(--glass-panel) !important;
 border:1px solid var(--glass-line) !important;
 border-radius:20px !important;
 box-shadow:var(--glass-shadow);
 backdrop-filter:blur(var(--glass-blur,20px)) saturate(125%);
 -webkit-backdrop-filter:blur(var(--glass-blur,20px)) saturate(125%);
}
#top-toolbar { padding:12px 16px; position:relative; z-index:1000; overflow:visible !important; }
#appearance-controls { overflow:visible !important; }
#editor-page #appearance-compact { z-index:1001; }
#appearance-compact > button + div { z-index:1002 !important; }
#editor-page > .tabs { padding:10px; }
#editor-page .tabitem { padding:20px 12px !important; }
.gradio-container .block { border-radius:14px !important; border-color:var(--glass-line) !important; }
#editor-page .block:not(.status-band) { background:var(--glass-panel); box-shadow:none; }
#editor-page .block:has(> .prose), #editor-page .block:has(> .app-title) { background:transparent; border:0; }
.gradio-container input:not([type=range]):not([type=checkbox]):not([type=radio]), .gradio-container textarea {
 background:var(--glass-field) !important;
 color:var(--body-text-color) !important;
 border-color:var(--glass-line) !important;
 border-radius:10px !important;
}
.gradio-container input:focus-visible, .gradio-container textarea:focus-visible,
.gradio-container button:focus-visible { outline:2px solid var(--page-accent) !important; outline-offset:3px; }
.gradio-container button { border-radius:11px !important; transition:background-color .16s,box-shadow .16s; }
.gradio-container button.primary { background:var(--page-accent) !important; color:var(--page-on-accent,#fff) !important; box-shadow:0 4px 12px rgba(40,123,98,.15); }
.gradio-container button.secondary { background:var(--glass-field) !important; border-color:var(--glass-line) !important; }
#editor-page [role=tablist] { padding:6px; gap:6px; border-bottom:1px solid var(--glass-line); }
#editor-page [role=tablist] button { padding:10px 18px; border:1px solid transparent !important; color:var(--body-text-color-subdued); }
#editor-page [role=tablist] button[aria-selected="true"] { background:var(--glass-field) !important; color:var(--page-accent) !important; border-color:var(--glass-line) !important; box-shadow:0 2px 8px rgba(38,73,58,.05); }
.dark #editor-page [role=tablist] button[aria-selected="true"] { color:var(--page-tab-accent,#a0e1c6) !important; }
body[data-appearance="dark"] .gradio-container button.secondary { background:var(--glass-field) !important; border-color:var(--glass-line) !important; }
.gradio-container .block.status-band { background:var(--glass-panel) !important; border-radius:12px !important; }
#studio-theme-columns > .column { border:1px solid var(--glass-line); border-radius:16px; background:var(--glass-panel); padding:12px; gap:10px !important; }
#editor-page #studio-theme-columns .block { background:transparent; border:0; padding:4px !important; }
#studio-theme-columns .prose p { font-size:13px; line-height:1.7; }
#editor-page #studio-theme-columns #studio-control-words > .block:first-child .prose p { font-size:14px; margin:0; }
#editor-page #studio-theme-columns .control-word-line .block { padding:0 !important; }
#studio-theme-columns .control-word-line textarea { padding:8px !important; }
#studio-reference-source { border:1px solid var(--glass-line); border-radius:16px; background:var(--glass-panel); padding:12px; gap:10px !important; }
#editor-page #studio-reference-source .block { background:transparent; border:0; padding:6px !important; }
#reference-source { padding:0 !important; }
#editor-page #reference-source-settings { align-items:flex-end; gap:12px !important; }
#reference-source { flex:0 0 auto !important; width:auto !important; }
#studio-reference-count { flex:0 1 448px !important; width:448px !important; min-width:min(340px,100%) !important; gap:0 !important; }
#editor-page #reference-count-header { align-items:center; gap:6px !important; flex-wrap:nowrap !important; }
#editor-page #reference-count-header .block { padding:0 !important; }
#reference-count-label { flex:0 0 32px !important; min-width:0 !important; }
#reference-count-label .prose p { margin:0; font-size:13px; white-space:nowrap; }
#reference-count-input { flex:0 0 64px !important; min-width:0 !important; }
#reference-count-input input { background:var(--glass-field) !important; border:1px solid var(--glass-line) !important; border-radius:8px !important; padding:6px !important; text-align:center; }
#reference-count-reset { flex:0 0 28px !important; width:28px !important; min-width:28px !important; padding:4px !important; }
#reference-shuffle { flex:0 0 60px !important; width:60px !important; min-width:60px !important; padding:4px !important; }
#reference-count-slider { flex:1 1 140px !important; min-width:100px !important; overflow:visible !important; }
#reference-count-slider .head { display:none !important; }
#reference-count-slider [data-testid=min-value], #reference-count-slider [data-testid=max-value] { display:none !important; }
#editor-page #reference-preview-panel { padding:0 !important; }
#reference-preview-panel .row { align-items:center; gap:8px !important; }
#studio-control-preview pre, #result-review-details pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:340px; overflow-y:auto; margin:8px 0; padding:12px; background:var(--glass-field); border:1px solid var(--glass-line); border-radius:8px; }
.result-review-card { border:1px solid var(--glass-line); border-radius:10px; padding:10px 12px; margin:8px 0; }
.result-review-card summary { cursor:pointer; font-weight:600; }
.review-score-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px; margin:12px 0; }
.round-prompt { margin-top:12px; padding-top:10px; border-top:1px solid var(--glass-line); }
#reference-folder-picker { align-self:flex-start; min-width:140px !important; }
#editor-controls { background:var(--glass-panel); border-radius:16px !important; }
.node-table { background:var(--glass-panel); border-radius:14px; overflow:hidden; }
.node-table th { background:var(--glass-field); font-weight:600; }
.node-table .selected-node { background:color-mix(in srgb,var(--page-accent) 10%,transparent); }
#appearance-compact > button + div { background:rgba(255,255,255,.96) !important; backdrop-filter:blur(var(--glass-blur,20px)); border-radius:14px !important; box-shadow:var(--glass-shadow); }
.dark #appearance-compact > button + div { background:rgba(var(--glass-popup-rgb,31,42,37),.97) !important; }
#result-gallery { background:var(--glass-panel); box-shadow:var(--glass-shadow); }
@media(prefers-reduced-motion:reduce) { .gradio-container button { transition:none; } }
@media(max-width:700px) {
#editor-page #appearance-controls { flex:1 1 100% !important; min-width:0 !important; width:100% !important; }
#editor-page #appearance-switch { flex:1 1 auto !important; width:auto !important; }
#top-toolbar { padding:10px; } #editor-page .tabitem { padding:12px 0 !important; } #editor-page [role=tablist] button { padding:9px 12px; } }
"""
