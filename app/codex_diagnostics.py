"""Release-only native transport diagnostic, isolated from real login state."""
import json
from pathlib import Path
from unittest.mock import patch


def self_test(folder):
    root=Path(folder).resolve();marker=root/'.biomed-codex-test'
    if root.exists() and any(root.iterdir()) and not marker.exists():
        raise ValueError('Codex diagnostics require an empty or marked test folder')
    root.mkdir(parents=True,exist_ok=True);marker.write_text('isolated Codex diagnostic',encoding='utf8')
    from codex_bridge import RPC
    from codex_runtime import executable
    # The native component manages its own empty home; no ~/.codex is read/copied.
    with patch('data_location.active_data_directory',return_value=root):
        rpc=RPC()
        try:
            account=rpc.request('account/read',{})
            if account.get('account') is not None:raise ValueError('Diagnostic home unexpectedly contains an account')
            report=dict(passed=True,component=executable(),account_is_empty=True)
        finally:rpc.close()
    (root/'codex-report.json').write_text(json.dumps(report),encoding='utf8')
    return 0
