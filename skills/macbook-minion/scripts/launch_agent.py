"""Opt-in Aqua lifecycle; the existing coordinator remains the sole queue owner."""
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import threading

import control
import local_context

LOG_LIMIT = 1024 * 1024


def _identity(config):
    identity = [str(Path(config['_config_path']).expanduser().absolute()),
                config['host'], config.get('_worker_id', 'default')]
    return 'com.macbookminion.coordinator.' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]


def agent_path(config):
    return Path.home() / 'Library' / 'LaunchAgents' / (_identity(config) + '.plist')


def _metadata_path(config):
    return Path(config['_state_dir']) / (_identity(config) + '.json')


def _private_info(info):
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077 and info.st_nlink == 1


def _read_private(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as source:
        if not _private_info(os.fstat(source.fileno())):
            raise OSError('Managed file does not match private ownership requirements.')
        data = source.read(16385)
        if len(data) > 16384:
            raise OSError('Managed metadata exceeds its size limit.')
        return data


def _private_directory(path):
    if path.is_symlink():
        raise OSError('Managed directory must not be a symlink.')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError('Managed state directory must be private and owned by this user.')


def _publish(path, data):
    # Hard-link publication is atomic and cannot overwrite a competing installation.
    descriptor, temporary = tempfile.mkstemp(prefix='.minion-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path, follow_symlinks=False)
    except FileExistsError:
        raise OSError('Managed file appeared during installation; retry after inspecting it.') from None
    finally:
        os.unlink(temporary)


def _metadata(config, create=False):
    path = _metadata_path(config)
    raw = _read_private(path)
    command = control.service_command(config)
    if raw is not None:
        try:
            saved = json.loads(raw)
            interpreter = saved['arguments'][0]
        except (ValueError, KeyError, TypeError, IndexError):
            raise OSError('LaunchAgent identity metadata does not match; no changes made.') from None
        if not isinstance(interpreter, str):
            raise OSError('LaunchAgent interpreter metadata is invalid.')
        command[0] = interpreter
    elif not create:
        raise OSError('LaunchAgent identity metadata is missing; no changes made.')
    interpreter = Path(command[0])
    if not interpreter.is_absolute() or not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise OSError('Pinned LaunchAgent interpreter is unavailable; repair the installation explicitly.')
    expected = {'version': 1, 'label': _identity(config), 'arguments': command,
                'state_dir': str(Path(config['_state_dir']).absolute())}
    canonical = (json.dumps(expected, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if raw is None:
        _publish(path, canonical)
    elif raw != canonical:
        raise OSError('LaunchAgent identity metadata does not match; no changes made.')
    return expected


def _canonical(config, metadata):
    return plistlib.dumps({
        'Label': _identity(config), 'ProgramArguments': metadata['arguments'],
        'WorkingDirectory': metadata['state_dir'], 'Umask': 0o077,
        'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null',
        'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
        'ThrottleInterval': 30, 'LimitLoadToSessionType': 'Aqua',
    }, sort_keys=True)


def _preflight():
    local_context.require_healthy()
    if os.getuid() == 0:
        raise OSError('Minion LaunchAgent requires a non-root Aqua user session.')


def _matching(config):
    path = agent_path(config)
    if path.is_symlink() or any(parent.is_symlink() for parent in (path.parent, path.parent.parent)):
        raise OSError('LaunchAgent path does not match a private managed file.')
    raw = _read_private(path)
    if raw is None:
        return False
    if raw != _canonical(config, _metadata(config)):
        raise OSError('Existing LaunchAgent does not match this configuration; no changes made.')
    return True


@contextmanager
def _lifecycle(config):
    _private_directory(Path(config['_state_dir']))
    path = _metadata_path(config).with_suffix('.lock')
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, 'r+b') as lock:
        if not _private_info(os.fstat(lock.fileno())):
            raise OSError('LaunchAgent operation lock must be private.')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError('Another LaunchAgent operation is active; retry when it finishes.') from None
        yield


def _launchctl(*arguments, allow_missing=False):
    try:
        result = subprocess.run(['/bin/launchctl', *arguments], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise OSError('LaunchAgent operation could not complete; use a healthy Terminal session.') from None
    if allow_missing and result.returncode == 113:
        return None
    if result.returncode:
        raise OSError('LaunchAgent ' + arguments[0] + ' failed; inspect the service in a healthy Terminal session.')
    return result.stdout


def _target(config):
    return 'gui/' + str(os.getuid()) + '/' + _identity(config)


def _loaded(config):
    output = _launchctl('print', _target(config), allow_missing=True)
    if output is None:
        return False
    paths = [line.strip()[7:] for line in output.splitlines() if line.strip().startswith('path = ')]
    if paths != [str(agent_path(config))]:
        raise OSError('Loaded LaunchAgent does not match this managed plist; no changes made.')
    return True


def _no_legacy_coordinator(config):
    try:
        descriptor = os.open(Path(config['_state_dir']) / 'service.lock', os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return
    with os.fdopen(descriptor) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError('An existing coordinator is running; stop it explicitly before installing or starting the LaunchAgent.') from None
        fcntl.flock(lock, fcntl.LOCK_UN)


def _require_matching(config):
    if not _matching(config):
        raise OSError('LaunchAgent file no longer matches; no changes made.')


def _start(config, loaded):
    _require_matching(config)
    if not loaded:
        _no_legacy_coordinator(config)
        _launchctl('bootstrap', 'gui/' + str(os.getuid()), str(agent_path(config)))
    _require_matching(config)
    _launchctl('kickstart', _target(config))


def install(config):
    """Publish private identity and plist files; start in the user's Aqua domain."""
    _preflight()
    with _lifecycle(config):
        exists = _matching(config)
        loaded = _loaded(config)
        if not loaded:
            _no_legacy_coordinator(config)
        if not exists:
            if loaded:
                raise OSError('Loaded LaunchAgent has no matching managed file; no changes made.')
            path = agent_path(config)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _publish(path, _canonical(config, _metadata(config, create=True)))
        _start(config, loaded)
    return {'state': 'installed', 'plist': str(agent_path(config)), 'label': _identity(config)}


def start_if_installed(config):
    if not _matching(config):
        return False
    _preflight()
    with _lifecycle(config):
        _require_matching(config)
        _start(config, _loaded(config))
    return True


def stop_if_installed(config):
    if not _matching(config):
        return False
    _preflight()
    with _lifecycle(config):
        _require_matching(config)
        if _loaded(config):
            _require_matching(config)
            _launchctl('bootout', _target(config))
    return True


class RuntimeLog(io.TextIOBase):
    """Bounded Python diagnostic stream; used only by the service.lock owner."""
    def __init__(self, config):
        self.path = Path(config['_state_dir']) / 'controller.log'
        self.lock = threading.RLock()

    def write(self, text):
        data = text[-LOG_LIMIT:].encode('utf-8', errors='replace')[-LOG_LIMIT:]
        with self.lock:
            _private_directory(self.path.parent)
            for path in (self.path, self.path.with_suffix('.log.1')):
                try:
                    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
                except FileNotFoundError:
                    continue
                with os.fdopen(descriptor, 'r+b') as existing:
                    info = os.fstat(existing.fileno())
                    if not _private_info(info):
                        raise OSError('Controller log must be a private regular file.')
                    if info.st_size > LOG_LIMIT:
                        existing.truncate(LOG_LIMIT)
            if self.path.exists() and self.path.stat().st_size + len(data) > LOG_LIMIT:
                self.path.replace(self.path.with_suffix('.log.1'))
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(descriptor, 'ab') as output:
                if not _private_info(os.fstat(output.fileno())):
                    raise OSError('Controller log ownership changed.')
                output.write(data)
        return len(text)

    def flush(self):
        pass  # Each write closes its file, including buffered data.


def configure_runtime(config):
    """Call after acquiring service.lock, before starting threads or the dashboard."""
    _preflight()
    _private_directory(Path(config['_state_dir']))
    stream = RuntimeLog(config)
    stream.write('Coordinator diagnostic log opened.\n')
    sys.stdout = sys.stderr = stream
