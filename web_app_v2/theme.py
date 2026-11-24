from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    product_badge: str
    sidebar_labels: tuple[str, str, str, str, str, str]
    css: str


_BASE_CSS = """
<style>
:root {{
  --ink: {ink}; --muted: {muted}; --surface: {surface}; --canvas: {canvas};
  --sidebar: {sidebar}; --sidebar-active: {sidebar_active}; --accent: {accent};
  --accent-ink: {accent_ink}; --border: {border}; --success: {success};
  --running: {running}; --failed: {failed}; --paused: {paused}; --idle: {idle};
}}
/* Keep the v2 reference palette stable when Edge/Windows is using dark mode. */
:root, html, body, .stApp, [data-baseweb="popover"], [role="listbox"] {{ color-scheme: light !important; }}
html, body, [class*="css"] {{ font-family: Inter, "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{ background-color: var(--canvas) !important; color: var(--ink) !important; }}
.stApp input:not([type="checkbox"]), .stApp textarea, .stApp select {{ color: var(--ink) !important; background-color: transparent !important; border: 0 !important; box-shadow: none !important; outline: none !important; color-scheme: light !important; }}
.stApp button, .stApp [role="button"] {{ color-scheme: light !important; }}
.stApp [data-testid="stCheckbox"] label, .stApp [data-testid="stWidgetLabel"] {{ color: var(--ink) !important; }}
.stApp [data-testid="stTooltipContent"], .stApp [role="tooltip"] {{ display: none !important; }}
.stApp::before {{ content: ""; position: fixed; inset: 0; pointer-events: none; z-index: -1; opacity: .56; background: {pattern}; }}
#MainMenu, footer, header {{ visibility: hidden; }}
.block-container {{ box-sizing: border-box; max-width: 1024px; padding: 42px 44px 34px; }}
.mfr-shell {{ display: grid; grid-template-columns: 190px minmax(0, 1fr); min-height: 680px; overflow: hidden; border: 1px solid var(--border); border-radius: 16px; background: var(--surface); box-shadow: 0 18px 40px rgba(18, 35, 52, .14); }}
.st-key-v2-sidebar {{ min-height: calc(100vh - 86px); height: auto; align-self: stretch; box-sizing: border-box; margin: 0; padding: 0 10px 14px; border: 1px solid color-mix(in srgb, var(--sidebar) 82%, #fff); border-radius: 10px; background: var(--sidebar); box-shadow: 0 16px 30px rgba(18, 35, 52, .14); }}
.st-key-v2-sidebar [data-testid="stVerticalBlock"] {{ gap: 0.2rem; }}
.mfr-brand {{ display: flex; gap: 9px; align-items: center; min-height: 60px; padding: 0 10px; border-bottom: 1px solid rgba(255,255,255,.12); color: #fff; background: transparent; font-size: .9rem; font-weight: 700; letter-spacing: -.015em; }}
.mfr-nav-heading {{ display: none; }}
.st-key-v2-sidebar .stButton {{ margin: 0; }}
.st-key-v2-sidebar .stButton button {{ justify-content: flex-start !important; min-height: 39px; width: 100% !important; padding: .42rem .72rem; border: 1px solid transparent; border-radius: 6px; color: rgba(255,255,255,.76); background: transparent; font-size: .74rem; font-weight: 560; line-height: 1.2; letter-spacing: .005em; box-shadow: none; text-align: left !important; white-space: nowrap; }}
.st-key-v2-sidebar .stButton button[data-testid="stBaseButton-secondary"] {{ color: rgba(255,255,255,.76); background: transparent; border-color: transparent; }}
.st-key-v2-sidebar .stButton button:hover {{ color: #fff; background: rgba(255,255,255,.09); border-color: rgba(255,255,255,.08); }}
.st-key-v2-sidebar .stButton button[data-testid="stBaseButton-primary"] {{ color: var(--accent-ink); background: var(--sidebar-active); border-color: var(--sidebar-active); box-shadow: none; font-weight: 680; }}
.st-key-v2-sidebar .stButton button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.st-key-v2-sidebar .stButton button > div, .st-key-v2-sidebar .stButton button span, .st-key-v2-sidebar .stButton button div[data-testid="stMarkdownContainer"], .st-key-v2-sidebar .stButton button div[data-testid="stMarkdownContainer"] > p {{ justify-content: flex-start !important; width: 100% !important; text-align: left !important; }}
.st-key-v2-sidebar .stButton button p {{ display: flex; align-items: center; gap: 9px; margin: 0; font-family: inherit; }}
.st-key-v2-sidebar .stButton button p:first-letter {{ font-size: 1.1rem; }}
.mfr-sidebar-footer {{ display: none; }}
.mfr-brand-mark {{ color: var(--accent); font-size: 1.3rem; line-height: 1; }}
.mfr-nav-item {{ padding: 10px 11px; margin: 4px 0; border-radius: 8px; color: rgba(255,255,255,.78); font-size: .88rem; }}
.mfr-nav-item.active {{ color: #fff; background: var(--sidebar-active); box-shadow: inset 0 0 0 1px rgba(255,255,255,.08); }}
.mfr-content {{ min-width: 0; padding: 20px 20px 25px; }}
.mfr-header {{ display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 0 0 16px; border-bottom: 1px solid var(--border); }}
.mfr-heading {{ margin: 0 !important; color: var(--ink) !important; font-size: 1.1rem !important; line-height: 1.1 !important; letter-spacing: -.035em !important; white-space: nowrap; }}
.mfr-subheading {{ color: var(--muted); font-size: .63rem; margin-top: 2px; }}
.mfr-badge {{ display: inline-flex; gap: 8px; align-items: center; padding: 7px 10px; border: 1px solid var(--border); border-radius: 9px; font-size: .76rem; color: var(--ink); background: color-mix(in srgb, var(--surface) 90%, var(--accent)); }}
.mfr-badge-mark {{ color: var(--accent); font-size: 1.15rem; }}
.mfr-card {{ margin-top: 16px; border: 1px solid var(--border); background: color-mix(in srgb, var(--surface) 96%, var(--accent)); border-radius: 7px; overflow: hidden; }}
.mfr-table {{ width: 100%; border-collapse: collapse; font-size: .84rem; }}
.mfr-table th {{ text-align: left; font-weight: 620; padding: 10px 11px; color: var(--ink); background: color-mix(in srgb, var(--surface) 88%, var(--accent)); border-bottom: 1px solid var(--border); white-space: nowrap; }}
.mfr-table td {{ padding: 10px 11px; border-bottom: 1px solid var(--border); vertical-align: middle; color: var(--ink); }}
.mfr-table tr:last-child td {{ border-bottom: 0; }}
.mfr-status {{ display: inline-flex; align-items: center; gap: 6px; font-weight: 650; white-space: nowrap; }}
.mfr-status.success {{ color: var(--success); }} .mfr-status.running {{ color: var(--running); }} .mfr-status.failed {{ color: var(--failed); }} .mfr-status.paused {{ color: var(--paused); }} .mfr-status.idle {{ color: var(--idle); }} .mfr-status.queued {{ color: var(--running); }}
.mfr-spinner {{ display: inline-block; animation: mfr-spin 1.1s linear infinite; }} @keyframes mfr-spin {{ to {{ transform: rotate(360deg); }} }}
.mfr-table .stButton > button {{ min-height: 2rem; padding: .2rem .45rem; border-radius: 7px; border: 1px solid var(--border); color: var(--ink); background: var(--surface); }}
.mfr-table .stButton > button:hover {{ border-color: var(--accent); color: var(--accent); }}
.mfr-toolbar {{ margin-top: 12px; }}
.mfr-toolbar .stButton > button {{ border-radius: 8px; background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }}
.mfr-bottom-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) 240px; gap: 15px; margin-top: 16px; }}
.mfr-section-title {{ margin: 0; padding: 10px 11px; font-size: .77rem; font-weight: 700; border-bottom: 1px solid var(--border); }}
.mfr-system {{ padding: 15px; }}
.mfr-system-title {{ font-size: .77rem; font-weight: 720; }}
.mfr-health {{ color: var(--success); font-size: .74rem; font-weight: 720; margin-top: 14px; }}
.mfr-muted {{ color: var(--muted); font-size: .79rem; }}
.mfr-progress {{ width: 100%; height: 6px; overflow: hidden; border-radius: 12px; background: color-mix(in srgb, var(--border) 65%, transparent); }}
.mfr-progress span {{ display: block; height: 100%; border-radius: inherit; background: var(--running); }}
.mfr-progress.running span {{ width: 68%; animation: mfr-pulse 1.4s ease-in-out infinite; }} .mfr-progress.queued span {{ width: 38%; background: var(--idle); }} .mfr-progress.complete span {{ width: 100%; background: var(--success); }} .mfr-progress.failed span {{ width: 100%; background: var(--failed); }}
@keyframes mfr-pulse {{ 50% {{ transform: translateX(28%); opacity: .72; }} }}
.mfr-controls {{ margin: 16px 0 0; padding: 2px; }}
.mfr-controls .stTextInput input, .mfr-controls .stSelectbox div[data-baseweb="select"] > div {{ background: var(--surface); }}
/* Reporting year/month selectors must not inherit Edge's dark native control palette. */
.st-key-v2-run-controls [data-testid="stWidgetLabel"] {{ color: var(--ink) !important; font-size: .78rem; font-weight: 600; }}
.st-key-v2-run-controls [data-baseweb="select"] > div {{ min-height: 28px !important; height: 28px !important; border: 1px solid var(--border) !important; border-radius: 5px !important; background: var(--surface) !important; box-shadow: none !important; color: var(--ink) !important; }}
.st-key-v2-run-controls [data-baseweb="select"] > div:hover {{ border-color: var(--accent) !important; }}
.st-key-v2-run-controls [data-baseweb="select"] input, .st-key-v2-run-controls [data-baseweb="select"] div {{ color: var(--ink) !important; }}
.st-key-v2-run-controls [data-baseweb="select"] svg {{ color: var(--ink) !important; fill: var(--ink) !important; }}
.st-key-v2-run-controls [data-baseweb="select"] > div:focus-within {{ border-color: var(--accent) !important; box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 22%, transparent) !important; }}
/* Streamlit's React Aria controls carry their visible outline on the root,
   rather than the native input. Style that root to prevent dark inset edges. */
.st-key-v2_operator [data-testid="stTextInputRootElement"], .st-key-v2_override_reason [data-testid="stTextInputRootElement"], .st-key-v2_search [data-testid="stTextInputRootElement"] {{ min-height: 27px !important; height: 27px !important; box-sizing: border-box !important; border: 1px solid var(--border) !important; border-radius: 5px !important; background: var(--surface) !important; box-shadow: none !important; overflow: hidden !important; }}
.st-key-v2_operator [data-testid="stTextInputRootElement"]:focus-within, .st-key-v2_override_reason [data-testid="stTextInputRootElement"]:focus-within, .st-key-v2_search [data-testid="stTextInputRootElement"]:focus-within {{ border-color: var(--accent) !important; box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent) !important; }}
.st-key-v2_operator input, .st-key-v2_override_reason input, .st-key-v2_search input {{ border: 0 !important; outline: 0 !important; box-shadow: none !important; background: transparent !important; }}
.st-key-v2_operator input::placeholder, .st-key-v2_override_reason input::placeholder, .st-key-v2_search input::placeholder {{ color: var(--muted) !important; opacity: .62 !important; }}
.st-key-v2-run-controls [data-testid="stSelectbox"] [role="group"] {{ min-height: 28px !important; height: 28px !important; box-sizing: border-box !important; border: 1px solid var(--border) !important; border-radius: 5px !important; background: var(--surface) !important; box-shadow: none !important; overflow: hidden !important; }}
.st-key-v2-run-controls [data-testid="stSelectbox"] [role="group"]:focus-within {{ border-color: var(--accent) !important; box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent) !important; }}
.st-key-v2-run-controls [data-testid="stSelectbox"] [role="group"] input {{ border: 0 !important; outline: 0 !important; box-shadow: none !important; background: transparent !important; }}
.st-key-v2-run-controls [data-testid="stSelectbox"] [role="group"] button {{ border: 0 !important; border-left: 1px solid var(--border) !important; border-radius: 0 !important; background: var(--surface) !important; color: var(--ink) !important; box-shadow: none !important; }}
/* BaseWeb mounts the listbox in a portal below body, outside the Streamlit app root. */
[data-baseweb="popover"], [role="listbox"] {{ color-scheme: light !important; background: var(--surface) !important; color: var(--ink) !important; }}
[data-baseweb="popover"] > div, [role="listbox"] {{ padding: 4px !important; border: 1px solid var(--border) !important; border-radius: 8px !important; box-shadow: 0 14px 30px rgba(18, 35, 52, .20) !important; }}
[role="option"] {{ min-height: 30px !important; padding: 5px 10px !important; border-radius: 5px !important; background: var(--surface) !important; color: var(--ink) !important; font-size: .83rem !important; }}
[role="option"]:hover, [role="option"][aria-selected="true"] {{ background: color-mix(in srgb, var(--accent) 15%, var(--surface)) !important; color: var(--ink) !important; }}
[role="option"][aria-selected="true"] {{ box-shadow: inset 3px 0 0 var(--accent); font-weight: 650 !important; }}
[role="option"]:focus-visible {{ outline: 2px solid var(--accent) !important; outline-offset: -2px; }}
/* Theme settings use Streamlit React Aria roots, whose default surface can
    otherwise follow the operating system's dark form treatment. */
.st-key-v2-theme-settings [data-testid="stExpander"] > details {{ overflow: hidden !important; border: 1px solid var(--border) !important; border-radius: 8px !important; background: var(--surface) !important; box-shadow: none !important; }}
.st-key-v2-theme-settings [data-testid="stExpander"] summary {{ min-height: 38px !important; background: var(--surface) !important; border: 0 !important; border-radius: 7px !important; color: var(--ink) !important; box-shadow: none !important; }}
.st-key-v2-theme-settings [data-testid="stExpander"] summary:hover, .st-key-v2-theme-settings [data-testid="stExpander"] summary:focus-visible {{ background: color-mix(in srgb, var(--accent) 7%, var(--surface)) !important; color: var(--ink) !important; }}
.st-key-v2-theme-settings [data-testid="stExpander"] summary:focus-visible {{ outline: 2px solid var(--accent) !important; outline-offset: -2px !important; }}
.st-key-v2-theme-settings [data-testid="stExpander"] details[open] summary {{ border-bottom: 1px solid var(--border) !important; border-radius: 7px 7px 0 0 !important; }}
.st-key-v2-theme-settings [data-testid="stExpander"] summary [data-testid="stIconMaterial"], .st-key-v2-theme-settings [data-testid="stExpander"] summary p {{ color: var(--ink) !important; }}
.st-key-v2-theme-settings [data-testid="stSelectbox"] [role="group"] {{ min-height: 32px !important; height: 32px !important; box-sizing: border-box !important; border: 1px solid var(--border) !important; border-radius: 6px !important; background: var(--surface) !important; color: var(--ink) !important; box-shadow: none !important; overflow: hidden !important; color-scheme: light !important; }}
.st-key-v2-theme-settings [data-testid="stSelectbox"] [role="group"]:focus-within {{ border-color: var(--accent) !important; box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 22%, transparent) !important; }}
.st-key-v2-theme-settings [data-testid="stSelectbox"] [role="group"] input {{ height: 30px !important; color: var(--ink) !important; background: transparent !important; border: 0 !important; outline: 0 !important; box-shadow: none !important; }}
.st-key-v2-theme-settings [data-testid="stSelectbox"] [role="group"] button {{ align-self: stretch !important; border: 0 !important; border-left: 1px solid var(--border) !important; border-radius: 0 !important; background: var(--surface) !important; color: var(--ink) !important; box-shadow: none !important; }}
.st-key-v2-theme-settings [data-testid="stWidgetLabel"] {{ color: var(--ink) !important; }}
/* Keep the requested raw Unicode action labels as legible, compact controls. */
div[class*="st-key-v2_run_"] .stButton, div[class*="st-key-v2_config_"] .stButton, div[class*="st-key-v2_schedule_"] .stButton, div[class*="st-key-v2_delete_"] .stButton {{ display: flex; justify-content: center; }}
div[class*="st-key-v2_run_"] .stButton button, div[class*="st-key-v2_config_"] .stButton button, div[class*="st-key-v2_schedule_"] .stButton button, div[class*="st-key-v2_delete_"] .stButton button {{ display: inline-flex !important; align-items: center !important; justify-content: center !important; width: 34px !important; height: 34px !important; min-height: 34px !important; padding: 0 !important; border: 1px solid var(--border) !important; border-radius: 7px !important; background: var(--sidebar) !important; color: #fff !important; font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif !important; font-size: 1rem !important; font-weight: 600 !important; line-height: 1 !important; box-shadow: none !important; }}
div[class*="st-key-v2_run_"] .stButton button:hover:not(:disabled), div[class*="st-key-v2_config_"] .stButton button:hover:not(:disabled), div[class*="st-key-v2_schedule_"] .stButton button:hover:not(:disabled), div[class*="st-key-v2_delete_"] .stButton button:hover:not(:disabled) {{ border-color: var(--accent) !important; background: var(--sidebar-active) !important; color: #fff !important; }}
div[class*="st-key-v2_run_"] .stButton button:focus-visible, div[class*="st-key-v2_config_"] .stButton button:focus-visible, div[class*="st-key-v2_schedule_"] .stButton button:focus-visible, div[class*="st-key-v2_delete_"] .stButton button:focus-visible {{ outline: 2px solid var(--accent) !important; outline-offset: 2px !important; }}
div[class*="st-key-v2_run_"] .stButton button:disabled, div[class*="st-key-v2_config_"] .stButton button:disabled, div[class*="st-key-v2_schedule_"] .stButton button:disabled, div[class*="st-key-v2_delete_"] .stButton button:disabled {{ border-color: var(--border) !important; background: color-mix(in srgb, var(--sidebar) 45%, var(--surface)) !important; color: var(--muted) !important; opacity: .75 !important; }}
div[class*="st-key-v2_run_"] .stButton button div, div[class*="st-key-v2_config_"] .stButton button div, div[class*="st-key-v2_schedule_"] .stButton button div, div[class*="st-key-v2_delete_"] .stButton button div, div[class*="st-key-v2_run_"] .stButton button p, div[class*="st-key-v2_config_"] .stButton button p, div[class*="st-key-v2_schedule_"] .stButton button p, div[class*="st-key-v2_delete_"] .stButton button p {{ display: flex !important; align-items: center !important; justify-content: center !important; width: 100% !important; margin: 0 !important; line-height: 1 !important; }}
/* Reference desktop composition for the Pipelines Overview. Keep these selectors
    tied to v2 keys so dialogs and the other navigation views keep their controls. */
.st-key-v2-sidebar {{ background: #172131; border-color: #172131; box-shadow: 0 12px 26px rgba(16, 28, 46, .16); }}
.st-key-v2-sidebar .stButton button[data-testid="stBaseButton-primary"] {{ background: #344258; border-color: #344258; color: #fff; }}
.st-key-v2-sidebar .stButton button:hover {{ background: #26364f; }}
.mfr-brand-mark {{ color: #2287dd; font-size: 1.08rem; }}
.mfr-badge {{ display: none; }}
.st-key-v2_new_pipeline .stButton button {{ min-height: 30px; padding: .3rem .55rem; border: 1px solid #1674c6; border-radius: 5px; background: #197dcc; color: #fff; font-size: .68rem; font-weight: 650; box-shadow: 0 2px 5px rgba(22, 93, 160, .2); white-space: nowrap; }}
.st-key-v2_new_pipeline .stButton button:hover {{ border-color: #0e66b3; background: #0e6fbe; color: #fff; }}
.st-key-v2_search [data-testid="stWidgetLabel"] {{ display: none; }}
.st-key-v2_search [data-testid="stTextInput"] {{ margin-top: 0; }}
.st-key-v2_search [data-testid="stTextInputRootElement"] {{ border-color: #cfd6df !important; }}
.st-key-v2_search input {{ min-height: 30px !important; padding: .25rem .55rem !important; font-size: .68rem !important; }}
.st-key-v2-pipeline-overview, .st-key-v2-active-runs, .st-key-v2-system-status {{ border: 1px solid #d9dee6 !important; border-radius: 7px !important; background: #fff !important; box-shadow: 0 2px 5px rgba(20, 33, 50, .05) !important; }}
.st-key-v2-pipeline-overview {{ margin-top: 8px; overflow: hidden; }}
.st-key-v2-pipeline-overview, .st-key-v2-active-runs {{ gap: 0 !important; }}
.mfr-panel-title {{ display: none; }}
.mfr-schedule-detail {{ display: none; }}
.st-key-v2-pipeline-overview [data-testid="stCaptionContainer"] {{ margin: 0; color: #253144; font-size: .58rem; }}
.st-key-v2-pipeline-overview [data-testid="stCaptionContainer"] p {{ margin: 0; }}
.st-key-v2-pipeline-overview [data-testid="stDivider"], .st-key-v2-active-runs [data-testid="stDivider"] {{ margin: .08rem 0 !important; }}
.st-key-v2-pipeline-overview hr, .st-key-v2-active-runs hr {{ margin: .1rem 0 !important; }}
.st-key-v2-pipeline-overview [data-testid="stHorizontalBlock"] {{ align-items: center; min-height: 30px; }}
.mfr-pipeline-name {{ margin: 0 !important; color: #182534; font-size: .62rem; font-weight: 650; line-height: 1.25; white-space: nowrap; }}
.mfr-pipeline-detail {{ display: none; }}
.st-key-v2-pipeline-overview .stMarkdown {{ margin: 0; }}
.st-key-v2-pipeline-overview [data-testid="stText"] {{ font-size: .6rem; }}
.st-key-v2-pipeline-overview [data-testid="stElementContainer"] {{ margin-bottom: 0 !important; }}
.st-key-v2-pipeline-overview .mfr-status {{ font-size: .6rem; gap: 4px; }}
.st-key-v2-pipeline-overview .mfr-status.success {{ color: #149251; }}
.st-key-v2-pipeline-overview .mfr-status.running, .st-key-v2-active-runs .mfr-status.running {{ color: #1678cb; }}
.st-key-v2-pipeline-overview .mfr-status.failed {{ color: #d63b43; }}
.st-key-v2-pipeline-overview .mfr-status.paused {{ color: #d28a17; }}
.st-key-v2-pipeline-overview .mfr-status.idle {{ color: #78828e; }}
.st-key-v2-pipeline-overview div[class*="st-key-v2_run_"] .stButton button, .st-key-v2-pipeline-overview div[class*="st-key-v2_config_"] .stButton button, .st-key-v2-pipeline-overview div[class*="st-key-v2_schedule_"] .stButton button, .st-key-v2-pipeline-overview div[class*="st-key-v2_delete_"] .stButton button {{ width: 24px !important; height: 24px !important; min-height: 24px !important; border: 1px solid #d5dce5 !important; border-radius: 5px !important; background: #fff !important; color: #4c5b6b !important; font-size: .7rem !important; }}
.st-key-v2-pipeline-overview div[class*="st-key-v2_run_"] .stButton button:hover:not(:disabled), .st-key-v2-pipeline-overview div[class*="st-key-v2_config_"] .stButton button:hover:not(:disabled), .st-key-v2-pipeline-overview div[class*="st-key-v2_schedule_"] .stButton button:hover:not(:disabled), .st-key-v2-pipeline-overview div[class*="st-key-v2_delete_"] .stButton button:hover:not(:disabled) {{ border-color: #9ab8d4 !important; background: #f2f7fc !important; color: #197dcc !important; }}
.st-key-v2-pipeline-overview div[class*="st-key-v2_delete_"] .stButton button {{ border-color: #efc7cb !important; background: #fff2f3 !important; color: #d7464d !important; }}
.st-key-v2-pipeline-overview div[class*="st-key-v2_delete_"] .stButton button:hover:not(:disabled) {{ border-color: #de747a !important; background: #ffe4e6 !important; color: #c92f38 !important; }}
.st-key-v2-active-runs {{ min-height: 90px; overflow: hidden; }}
.st-key-v2-active-runs [data-testid="stCaptionContainer"] {{ color: #253144; font-size: .62rem; }}
.st-key-v2-active-runs [data-testid="stText"], .st-key-v2-active-runs .mfr-status {{ font-size: .64rem; }}
.st-key-v2-system-status {{ min-height: 90px; padding: 0 10px !important; }}
.st-key-v2-system-status .mfr-system-title {{ margin: 10px 0 0; }}
.st-key-v2-system-status .mfr-health {{ margin: 12px 0 4px; }}
.st-key-v2-system-status [data-testid="stText"] {{ font-size: .66rem; }}
.st-key-v2-system-status [data-testid="stAlert"] {{ margin-top: 6px; padding: .25rem .4rem; font-size: .58rem; }}
.st-key-v2-run-controls {{ margin-top: 13px; border: 1px solid #d9dee6 !important; border-radius: 7px !important; background: #fff !important; }}
.mfr-run-controls-title {{ margin: 0; color: #182534; font-size: .75rem; font-weight: 700; }}
.st-key-v2-run-controls [data-testid="stCaptionContainer"] {{ font-size: .62rem; }}
.st-key-v2-run-controls [data-testid="stWidgetLabel"] {{ font-size: .66rem; }}
.st-key-v2-run-controls input {{ min-height: 27px !important; height: 27px !important; padding-top: 0 !important; padding-bottom: 0 !important; font-size: .68rem !important; }}
.st-key-v2_override [data-testid="stCheckbox"] label {{ white-space: nowrap; }}
.st-key-v2_override [data-testid="stCheckbox"] label > div:first-of-type {{ width: 16px !important; height: 16px !important; box-sizing: border-box !important; border: 1px solid #111 !important; border-radius: 3px !important; background: #fff !important; color: #111 !important; }}
.st-key-v2_override [data-testid="stCheckbox"] label[data-selected="true"] > div:first-of-type {{ background: #fff !important; border-color: #111 !important; }}
.st-key-v2_override [data-testid="stCheckbox"] label > div:first-of-type svg {{ width: 12px !important; height: 10px !important; stroke: #111 !important; color: #111 !important; }}
.st-key-v2_override [data-testid="stCheckbox"] label > div:first-of-type svg polyline {{ fill: none !important; stroke: #111 !important; stroke-width: 1.8 !important; }}
.st-key-v2_override [data-testid="stCheckbox"] label:focus-visible > div:first-of-type {{ outline: 2px solid var(--accent) !important; outline-offset: 2px !important; }}
.st-key-v2_override {{ transform: translateY(-8px); }}
.st-key-v2_override_reason {{ margin-top: -20px !important; }}
.st-key-v2-run-controls .stButton button {{ min-height: 31px; border-radius: 5px; background: #197dcc; border-color: #197dcc; font-size: .67rem; }}
/* Period Flow is a wider, read-only run-history matrix inside the existing v2 shell. */
body:has(.st-key-v2-period-flow) .block-container {{ max-width: 1500px; padding: 30px 32px 32px; }}
.st-key-v2-period-flow {{ margin-top: 8px; }}
.st-key-v2-period-flow [data-testid="stMetricLabel"] {{ color: #647184 !important; }}
.st-key-v2-period-flow [data-testid="stMetricValue"] {{ color: #17283b !important; }}
.st-key-v2-period-flow [data-testid="stMetricDelta"] {{ color: #647184 !important; }}
.mfr-flow-intro {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; margin: 8px 0 16px; }}
.mfr-flow-overline {{ margin: 0 0 5px; color: #197dcc; font-size: .62rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }}
.mfr-flow-description {{ margin: 0; color: #687489; font-size: .76rem; line-height: 1.5; }}
.mfr-preview-label {{ display: inline-flex; align-items: center; flex: 0 0 auto; padding: 6px 9px; border: 1px solid #d5e5f4; border-radius: 999px; background: #eff7fd; color: #32658f; font-size: .6rem; font-weight: 700; letter-spacing: .045em; white-space: nowrap; }}
.st-key-v2-period-flow [data-testid="stHorizontalBlock"] {{ align-items: stretch; }}
.mfr-period-grid-header {{ margin: 8px 0 6px; padding: 0 9px; }}
.mfr-period-heading {{ min-height: 35px; color: #647184; font-size: .59rem; line-height: 1.25; }}
.mfr-period-heading-index {{ display: block; margin-bottom: 4px; color: #197dcc; font-size: .55rem; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }}
.mfr-period-heading-name {{ display: block; color: #344256; font-size: .62rem; font-weight: 700; }}
.st-key-v2-period-grid {{ overflow-x: auto; }}
.st-key-v2-period-grid div[class*="st-key-v2-period-row-"] {{ margin-bottom: 8px; padding: 10px 9px 8px; border: 1px solid #dce3eb; border-radius: 9px; background: #fff; box-shadow: 0 2px 6px rgba(20, 33, 50, .035); }}
.st-key-v2-period-grid div[class*="st-key-v2-period-row-"]:hover {{ border-color: #bdcfe0; box-shadow: 0 5px 14px rgba(20, 33, 50, .065); }}
.st-key-v2-period-grid div[class*="st-key-v2-period-row-"] [data-testid="stHorizontalBlock"] {{ align-items: center; }}
.mfr-period-month {{ margin: 0 0 3px; color: #17283b; font-size: .75rem; font-weight: 740; line-height: 1.25; }}
.mfr-period-code {{ margin: 0 0 7px; color: #7a8797; font-size: .59rem; font-variant-numeric: tabular-nums; }}
.mfr-period-updated {{ margin: 4px 0 0; color: #7a8797; font-size: .55rem; line-height: 1.35; }}
.mfr-flow-pill {{ display: inline-flex; align-items: center; min-height: 20px; padding: 2px 7px; border-radius: 999px; font-size: .57rem; font-weight: 730; line-height: 1.2; white-space: nowrap; }}
.mfr-flow-pill.success {{ color: #137546; background: #e8f5ed; }}
.mfr-flow-pill.running {{ color: #176ead; background: #e8f3fb; }}
.mfr-flow-pill.failed {{ color: #b8323d; background: #fdecee; }}
.mfr-flow-pill.queued, .mfr-flow-pill.waiting {{ color: #667085; background: #f0f2f5; }}
.mfr-flow-pill.template {{ color: #7051a2; background: #f1ecf8; }}
.mfr-flow-detail {{ display: block; margin-top: 5px; color: #758194; font-size: .55rem; line-height: 1.35; overflow-wrap: anywhere; }}
.st-key-v2-period-flow div[class*="st-key-v2_period_view_"] .stButton button, .st-key-v2-period-flow div[class*="st-key-v2_period_run_"] .stButton button {{ min-height: 27px; padding: .25rem .4rem; border: 1px solid #d4dfeb; border-radius: 6px; background: #f6f9fc; color: #31516f; font-size: .61rem; font-weight: 650; white-space: nowrap; box-shadow: none; }}
.st-key-v2-period-flow div[class*="st-key-v2_period_view_"] .stButton button:hover, .st-key-v2-period-flow div[class*="st-key-v2_period_run_"] .stButton button:hover {{ border-color: #8eb6d8; background: #eef6fc; color: #176eae; }}
.st-key-v2-period-flow div[class*="st-key-v2_period_run_"] .stButton button {{ border-color: #197dcc; background: #197dcc; color: #fff; }}
.st-key-v2-period-flow div[class*="st-key-v2_period_run_"] .stButton button:hover {{ border-color: #0e66b3; background: #0e6fbe; color: #fff; }}
.mfr-flow-legend {{ display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin: 12px 0 2px; color: #748093; font-size: .62rem; }}
.mfr-flow-legend .mfr-flow-pill {{ margin-left: 3px; }}
@media (max-width: 1100px) {{
    body:has(.st-key-v2-period-flow) .block-container {{ padding: .8rem; }}
    .st-key-v2-period-grid [data-testid="stHorizontalBlock"] {{ min-width: 700px; }}
    .st-key-v2-period-grid .mfr-period-heading-name {{ font-size: .55rem; }}
    .st-key-v2-period-grid .mfr-flow-pill {{ padding: 2px 5px; font-size: .51rem; }}
    .st-key-v2-period-grid .mfr-flow-detail, .st-key-v2-period-grid .mfr-period-updated {{ font-size: .49rem; }}
    .st-key-v2-period-flow div[class*="st-key-v2_period_view_"] .stButton button,
    .st-key-v2-period-flow div[class*="st-key-v2_period_run_"] .stButton button {{ padding: .18rem .2rem; font-size: .53rem; }}
    .mfr-flow-intro {{ flex-direction: column; gap: 8px; }}
}}
@media (max-width: 850px) {{ .mfr-shell {{ grid-template-columns: 1fr; }} .mfr-sidebar {{ display: none; }} .st-key-v2-sidebar {{ min-height: auto; height: auto; }} .mfr-bottom-grid {{ grid-template-columns: 1fr; }} .mfr-header {{ align-items: flex-start; flex-direction: column; }} .mfr-table-wrap {{ overflow-x: auto; }} .mfr-table {{ min-width: 760px; }} .block-container {{ padding: .7rem; }} }}
</style>
"""


def _css(**tokens: str) -> str:
    return _BASE_CSS.format(**tokens)


THEMES: dict[str, Theme] = {
    "Default": Theme(
        key="minimal",
        title="Pipelines Overview",
        product_badge="MFR monthly data workflows",
        sidebar_labels=("Period Flow", "Pipelines", "Scheduled Tasks", "Activity Logs", "Configuration", "Settings"),
        css=_css(
            ink="#17233b", muted="#687489", surface="#ffffff", canvas="#f6f7fa",
            sidebar="#172131", sidebar_active="#344258", accent="#197dcc", accent_ink="#ffffff",
            border="#d9dee6", success="#169556", running="#197dcc", failed="#d9363e", paused="#d99018", idle="#7b8492",
            pattern="radial-gradient(circle at 93% 7%, rgba(25,118,210,.12) 0 1px, transparent 1px 11px)",
        ),
    ),
    "Snowflake": Theme(
        key="snowflake",
        title="Pipelines Overview",
        product_badge="Data Pipelines Platform · winter blue style",
        sidebar_labels=("Period Flow", "Pipelines", "Scheduled Tasks", "Activity Logs", "Configuration", "Settings"),
        css=_css(
            ink="#113450", muted="#5d7890", surface="#ffffff", canvas="#edfaff",
            sidebar="#073f70", sidebar_active="#12a9da", accent="#12a9da", accent_ink="#ffffff",
            border="#bae0ee", success="#078b63", running="#129eca", failed="#cb4155", paused="#c98b20", idle="#6f8190",
            pattern="linear-gradient(30deg, transparent 48%, rgba(18,169,218,.14) 49%, rgba(18,169,218,.14) 51%, transparent 52%) 0 0/76px 76px, linear-gradient(150deg, transparent 48%, rgba(18,169,218,.1) 49%, rgba(18,169,218,.1) 51%, transparent 52%) 0 0/76px 76px",
        ),
    ),
    "Databricks": Theme(
        key="databricks",
        title="Workflow Overview",
        product_badge="Workflow Orchestration · brick style",
        sidebar_labels=("Period Flow", "Workflows", "Jobs", "Job Runs", "Configuration", "Workflow Settings"),
        css=_css(
            ink="#2b2527", muted="#776e70", surface="#ffffff", canvas="#fff8f5",
            sidebar="#142d31", sidebar_active="#344d50", accent="#d8493d", accent_ink="#ffffff",
            border="#e6d6d0", success="#148152", running="#2387b5", failed="#c93637", paused="#c57d19", idle="#7b7776",
            pattern="repeating-linear-gradient(150deg, transparent 0 29px, rgba(216,73,61,.10) 30px 32px, transparent 33px 61px)",
        ),
    ),
}
