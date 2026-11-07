"""General failure evidence and no-replay regressions; no model weights are loaded."""
from __future__ import annotations

import base64
import copy
import json
import os
import queue
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import httpx
import pytest

from evaluation import campaign_lifecycle as lifecycle
from evaluation import disposable_followup_matrix as matrix
from evaluation import test_context_and_campaign_regressions as transport
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_smoke_runtime_repairs as bridges
from evaluation.generation_failures import independent_continuation
from runtime import request_timing
from runtime.request_failures import deliver_request_failure
from runtime.server_lifetime import exclusive, powershell_launch, prepare, snapshot
from runtime.tool_results import model_tool_result
from runtime.transport_failures import (
    BridgeFailure,
    TransportFailure,
    error_summary,
    transport_failure,
)

runtime = transport.runtime
variant = receipts.variant
bridge_type = bridges.bridge_type


def test_error_summary_preserves_raw_clixml():
    raw = '#< CLIXML\n<Objs><S S="Error">Receive failed_x000D__x000A_</S></Objs>'
    error = BridgeFailure(raw, receipt={'helper': {'actual_wait': True, 'exit_code': 1}})
    assert str(error) == 'Receive failed' and error.raw_detail == raw
    assert error_summary('<!DOCTYPE forbidden>') == '<!DOCTYPE forbidden>'


@pytest.mark.parametrize('code', [0, 1])
def test_bridge_receipt_tracks_actual_helper_not_backend(bridge_type, monkeypatch, code):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, code, '{"ok":true}' if code == 0 else '', 'reset' if code else '')

    monkeypatch.setattr(subprocess, 'run', run)
    bridge = bridge_type()
    if code:
        with pytest.raises(BridgeFailure):
            bridge.post_json('/v1/chat/completions', {})
    else:
        assert bridge.post_json('/v1/chat/completions', {}) == {'ok': True}
    assert len(calls) == 1
    assert bridge._qubitz_bridge_receipt['helper']['actual_wait'] is True
    assert bridge._qubitz_bridge_receipt['helper']['exit_code'] == code


@pytest.mark.parametrize('failure', [httpx.ReadError('reset'), httpx.ReadTimeout('late')])
def test_nonstreaming_failure_is_typed_and_never_replayed(runtime, failure):
    _base, client, _ns = runtime
    calls = []

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure

    client.transports = [SimpleNamespace(label='direct', post_json=post),
                         SimpleNamespace(label='forbidden', post_json=lambda *_: pytest.fail('replayed'))]
    with pytest.raises(TransportFailure) as captured:
        client.chat(**transport.request())
    assert len(calls) == 1
    assert captured.value.receipt['retry_permitted'] is False
    assert captured.value.receipt['completion_outcome'] == 'unknown'


def test_typed_gui_failure_still_marks_infrastructure_and_preserves_uncertainty(tmp_path, monkeypatch):
    monkeypatch.setattr('runtime.request_failures.retain_failure', lambda _: None)
    error = transport_failure(RuntimeError('reset'), {}, transport='fixture', endpoint='/chat')
    gui = SimpleNamespace(agent=SimpleNamespace(), event_queue=queue.Queue(), _qubitz_gui_log_event=lambda *_a, **_kw: None)
    assert deliver_request_failure(gui, error, 1) is False
    assert '[Completion status: partial]' in gui.event_queue.get()[2]
    row = {'status': 'failed', 'issues': []}
    diagnostic = {'behavior_issues': ['gui_logged_error'], 'request_failures': [gui.agent._last_request_failure]}
    matrix.apply_gui_diagnostics(row, diagnostic)
    assert row['status'] == 'harness_error'
    assert row['issues'] == ['generation_transport_error']
    assert row['runtime_failure']['model_correctness_verdict'] == 'not_established'
    assert not independent_continuation({'status': 'failed', 'cleanup': {'exit_code': 0},
        'turns': [{'observed': {'request_failure': gui.agent._last_request_failure}}]})['allowed']


@pytest.mark.parametrize('uncertain,cleanup,allowed', [(False, 0, True), (True, 0, False), (False, 1, False)])
def test_isolated_known_failure_only_permits_independent_next_tuple(uncertain, cleanup, allowed):
    row = {'status': 'failed', 'cleanup': {'exit_code': cleanup}, 'turns': [{'observed': {'request_failure': {
        'schema': 'qubitz_generation_rejection_v1', 'uncertain_prior_action': uncertain}}}]}
    assert independent_continuation(row)['allowed'] is allowed
    assert independent_continuation(row)['retry_permitted'] is False


@pytest.mark.parametrize('text', ['x' * 20000, '\u4e16\n\r' * 15000, '"actual_wait":true' * 3000])
def test_large_tool_result_is_valid_bounded_and_retains_scope(text):
    payload = {'is_error': False, 'structured_content': {'stdout': text, 'returncode': 0,
               'execution_receipt': {'actual_wait': True, 'supervisor_actual_wait': True,
               'identity_scope': 'containment_not_program', 'returncode_scope': 'shell_command',
               'output_retention': {'huge': text}}}, 'content_text': text}
    old = copy.deepcopy(payload)
    raw = model_tool_result(payload)
    result = json.loads(raw)
    assert len(raw) <= 6000 and payload == old
    assert result['evidence']['identity_scope'] == 'containment_not_program'
    assert result['evidence']['returncode_scope'] == 'shell_command'
    assert result['evidence']['actual_wait'] is True and result['evidence']['returncode'] == 0
    assert 'output_retention' in result['omitted_fields']


def test_summary_does_not_fabricate_missing_interpreter_or_identity():
    result = json.loads(model_tool_result({'structured_content': {'stdout': 'a'*10000}}))
    assert not {'actual_wait', 'identity_scope', 'interpreter'} & result['evidence'].keys()


def test_minimum_summary_budget_is_still_valid_json():
    payload = {'structured_content': {'stdout': 'x'*10000, 'argv':['a'*10000],
               'execution_receipt': {'actual_wait':True, 'identity_scope':'x'*10000}}}
    raw = model_tool_result(payload,512)
    assert len(raw) <= 512 and json.loads(raw)['truncated']


def test_bridge_observed_http_evidence_is_not_lost_to_helper_metadata():
    error = BridgeFailure('HTTP 422',receipt={'http_status':422,'http_body':'rejected'})
    result = transport_failure(error,{},transport='windows-bridge',endpoint='/chat',
                               bridge={'helper':{'actual_wait':True,'exit_code':0}})
    assert result.receipt['http_status'] == 422 and result.receipt['http_body'] == 'rejected'
    assert result.receipt['helper']['actual_wait'] is True


def test_phase_timing_never_infers_response_from_finish(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(request_timing.time, 'perf_counter', lambda: now[0])
    with request_timing.request_timings() as events:
        request_timing.observed('request')
        now[0] = 8.0
        request_timing.observed('error')
    assert request_timing.durations(events) == {'model_time_to_error_seconds': 7.0}
    with request_timing.request_timings() as events:
        request_timing.observed('request')
        now[0] = 10.0
        request_timing.observed('first_data')
        now[0] = 13.0
        request_timing.observed('response')
    assert request_timing.durations(events) == {'first_model_data_seconds': 2.0, 'first_model_response_seconds': 5.0}


def test_exact_literal_server_arguments_and_existing_owner_are_preserved(tmp_path):
    line = "# QUBITZ_OWNER=fixture\nSet-Location -LiteralPath 'C:\\task'\n& 'C:\\llama-server.exe' '--model' 'C:\\a b.gguf' '--ctx-size' '16384'"
    command = ['powershell.exe', '-NoProfile', '-EncodedCommand', base64.b64encode(line.encode('utf-16le')).decode()]
    server = SimpleNamespace(runtime_workspace=tmp_path, _qubitz_owner_token='fixture')
    result = prepare(server, command, str)
    intent = json.loads((server._qubitz_lifetime_directory/'intent.json').read_bytes())
    assert intent['command'] == ['C:\\llama-server.exe','--model','C:\\a b.gguf','--ctx-size','16384']
    script = base64.b64decode(result[-1]).decode('utf-16le')
    assert '# QUBITZ_OWNER=fixture' in script and '$child.WaitForExit()' in script
    assert '$child.ExitCode' in script and "'native_exit.json'" in script
    assert snapshot(server)['native_exit'] == 'unknown'


def test_server_receipt_rejects_pid_or_owner_reuse(tmp_path):
    server = SimpleNamespace(_qubitz_lifetime_directory=tmp_path)
    identity = {'pid': 42, 'owner_token': tmp_path.name, 'creation_ticks': '1'}
    exclusive(tmp_path/'native_launch.json', identity)
    exclusive(tmp_path/'native_exit.json', {'identity': {**identity,'creation_ticks':'2'}, 'actual_wait':True,'exit_code':0})
    assert snapshot(server)['native_exit'] == 'unknown'


def test_canonical_wait_overrides_stale_running_state_and_respects_user_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, 'ROOT', tmp_path)
    directory = tmp_path/'run_supervision';directory.mkdir()
    identity = {'pid': 1, 'creation_ticks': 'one', 'command_line': 'fixture'}
    exclusive(directory/'launch_receipt.json', {'identity':identity})
    exclusive(directory/'work_state.json', {'identity':identity,'status':'running'})
    exclusive(directory/'coordinator_exit.json', {'identity':identity,'actual_wait':True,'exit_code':2})
    outer = tmp_path/'outer.json'
    exclusive(outer, {'status':'running','canonical_supervision':directory.name})
    result = lifecycle.read_control_state(outer)
    assert result['status'] == 'exit_recorded_checkpoint_unknown' and result['running_verified'] is False
    outer.write_text(json.dumps({'canonical_supervision':directory.name,'user_resume_required':True}))
    assert lifecycle.read_control_state(outer)['status'] == 'paused_by_user'


def test_all_public_composed_runtime_uses_bounded_tool_summary(variant):
    source = variant._APP.base.AgentRunner.__module__
    assert source
    from runtime import read_variant_source
    text = read_variant_source(Path(variant.__file__))
    assert 'model_tool_result(' in text and 'durations(request_events)' in text


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows child-handle test runs separately on Windows')
def test_native_windows_lifetime_actual_wait_and_exact_argv(tmp_path):
    code = "import sys,json;print(json.dumps(sys.argv[1:]));print('fixture stderr',file=sys.stderr);sys.exit(7)"
    args = ['space value', 'quote"value', 'back\\slash', 'trailing\\']
    script = powershell_launch([sys.executable, '-B', '-c', code, *args], str(tmp_path), 'native-fixture')
    command = ['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 7
    assert json.loads(completed.stdout.strip()) == args
    assert 'fixture stderr' in completed.stderr
    launch = json.loads((tmp_path/'native_launch.json').read_bytes())
    waited = json.loads((tmp_path/'native_exit.json').read_bytes())
    assert waited['identity'] == launch and waited['actual_wait'] and waited['exit_code'] == 7
    assert waited['shutdown_requested'] is False and launch['parent_pid'] > 0


@pytest.mark.skipif(os.name != 'nt', reason='Native loopback bridge test runs separately on Windows')
def test_native_bridge_reset_http_error_and_malformed_response_no_replay(bridge_type):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            calls.append(self.path)
            if self.path == '/reset':
                self.connection.close()
                self.close_connection = True
                return
            raw = b'not json' if self.path == '/malformed' else b'{"error":{"message":"fixture rejection"}}'
            self.send_response(200 if self.path == '/malformed' else 422)
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever);thread.start()
    try:
        bridge=bridge_type(f'http://127.0.0.1:{server.server_port}')
        for endpoint in ['/reset','/error','/malformed']:
            with pytest.raises(RuntimeError):
                bridge.post_json(endpoint,{})
        assert calls == ['/reset','/error','/malformed']
    finally:
        server.shutdown();server.server_close();thread.join(5)
    assert not thread.is_alive()
