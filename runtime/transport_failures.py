"""Transport evidence is descriptive, never permission to replay a request."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from xml.etree import ElementTree


def error_summary(text: str) -> str:
    text = str(text)
    if '#< CLIXML' in text and '<!ENTITY' not in text and '<!DOCTYPE' not in text:
        try:
            root = ElementTree.fromstring(text[text.index('<Objs'):][:65536])
            errors = [''.join(node.itertext()) for node in root.iter() if node.get('S') == 'Error']
            if errors:
                text = ' '.join(errors)
        except (ValueError, ElementTree.ParseError):
            pass
    text = re.sub(r'_x([0-9A-Fa-f]{4})_', lambda match: chr(int(match[1], 16)), text)
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', text).strip()[:2048]


class BridgeFailure(RuntimeError):
    def __init__(self, detail: str, *, receipt: dict) -> None:
        self.receipt = dict(receipt)
        self.raw_detail = detail
        super().__init__(error_summary(detail))


class TransportFailure(RuntimeError):
    def __init__(self, message: str, *, receipt: dict) -> None:
        self.receipt = dict(receipt)
        super().__init__(message)


def retain_failure(error: TransportFailure) -> None:
    """Preserve raw diagnostics once; recording failure cannot replay generation."""
    try:
        directory = Path(__file__).resolve().parents[1] / '.cache/request_failures'
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or directory.resolve().parent.name != '.cache':
            raise ValueError('Failure evidence directory is aliased')
        if shutil.disk_usage(directory).free <= 20 * 1024**3:
            raise OSError('20 GiB reserve prevents diagnostic retention')
        path = directory / (error.receipt['request_id'] + '.json')
        raw = json.dumps(error.receipt, ensure_ascii=True, default=str).encode()
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        error.receipt['retained_failure'] = {'path': str(path), 'bytes': len(raw),
                                            'sha256': hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, KeyError) as exc:
        error.receipt['retention_error'] = str(exc)


def transport_failure(error: Exception, payload: dict, *, transport: str, endpoint: str,
                      backend=None, bridge=None) -> TransportFailure:
    evidence = {**(bridge or {}), **(getattr(error, 'receipt', {}) or {})}
    status = evidence.get('http_status')
    response = getattr(error, 'response', None)
    if response is not None:
        status = getattr(response, 'status_code', status)
        if evidence.get('http_body') is None:
            try:
                raw_body = response.content
                if isinstance(raw_body, bytes):
                    evidence['http_body'] = raw_body[:8192].decode('utf-8', errors='replace')
            except Exception as capture_error:  # noqa: BLE001 - preserve the original transport failure
                evidence['body_capture_error'] = type(capture_error).__name__
    receipt = {
        'schema': 'qubitz_transport_failure_v1', 'scope': 'infrastructure_unknown',
        'category': 'http_response_error' if type(status) is int else 'dispatch_outcome_unknown',
        'request_id': uuid.uuid4().hex,
        'request_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                                   separators=(',', ':')).encode()).hexdigest(),
        'transport': str(transport), 'endpoint': endpoint, 'backend': backend,
        'dispatch_phase': 'attempted_outcome_unknown', 'http_status': status,
        'http_body': evidence.get('http_body'), 'helper': evidence.get('helper'),
        'body_capture_error': evidence.get('body_capture_error'),
        'original_exception': type(error).__name__, 'detail': error_summary(str(error)),
        'completion_accepted': False, 'tool_call_accepted': False, 'retry_permitted': False,
        'completion_outcome': 'unknown',
    }
    raw_detail = str(getattr(error, 'raw_detail', error))
    receipt.update(raw_detail=raw_detail[:65536], raw_detail_complete=len(raw_detail) <= 65536,
                   raw_detail_sha256=hashlib.sha256(raw_detail.encode()).hexdigest())
    failure = TransportFailure(
        'Generation transport failed after dispatch; completion is unknown and the request was not replayed. '
        + receipt['detail'], receipt=receipt,
    )
    return failure
