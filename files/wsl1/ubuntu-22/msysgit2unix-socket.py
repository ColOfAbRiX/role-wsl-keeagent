#!/usr/bin/env python3

import argparse
import asyncio
import atexit
import errno
import os
import re
import signal
import socket
import sys

# Global verbose flag
VERBOSE = False

def log(msg):
    """Print debug message if verbose mode is enabled."""
    if VERBOSE:
        print(f"[msysgit2unix-socket] {msg}", file=sys.stderr)
        sys.stderr.flush()

def pid_exists(pid):
    """Check whether pid exists in the current process table."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as err:
        return err.errno != errno.ESRCH
    return True

def load_tcp_port(path):
    """Extracts the TCP port from the msysGit socket file."""
    log(f"Reading msysgit socket file: {path}")
    try:
        with open(path, 'r') as f:
            content = f.read()
            m = re.search(r'>(\d+)', content)
            if m:
                port = int(m.group(1))
                log(f"Extracted port: {port}")
                return port
            raise ValueError(f"Could not extract port from: {content}")
    except Exception as e:
        log(f"Error reading socket file: {e}")
        raise

def get_target_ip(ip_file_path):
    """Reads the IP from the specified file, defaults to 127.0.0.1 if none."""
    if not ip_file_path:
        return '127.0.0.1'
    try:
        with open(ip_file_path, 'r') as f:
            ip = f.read().strip()
            if ip:
                log(f"Using IP from file {ip_file_path}: {ip}")
                return ip
    except Exception as e:
        log(f"Warning: Could not read IP file {ip_file_path} ({e}). Falling back to 127.0.0.1")
    return '127.0.0.1'

async def proxy_data(reader, writer, buffer_size, label):
    """Generic pipe to move data from reader to writer."""
    try:
        while True:
            data = await reader.read(buffer_size)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception as e:
        log(f"Proxy error ({label}): {e}")
    finally:
        if not writer.is_closing():
            writer.close()

async def handle_unix_client(unix_reader, unix_writer, upstream_path, config):
    """Handles new Unix socket connection and bridges to the dynamic IP."""
    log("New connection accepted on Unix socket")
    try:
        port = load_tcp_port(upstream_path)
        target_ip = get_target_ip(config.ip_file)

        log(f"Connecting to {target_ip}:{port} (timeout: {config.timeout}s)")

        # Restore timeout logic using asyncio.wait_for
        tcp_reader, tcp_writer = await asyncio.wait_for(
            asyncio.open_connection(target_ip, port),
            timeout=config.timeout
        )
        log("TCP connection established")

        await asyncio.gather(
            proxy_data(unix_reader, tcp_writer, config.downstream_buffer_size, "Unix -> TCP"),
            proxy_data(tcp_reader, unix_writer, config.upstream_buffer_size, "TCP -> Unix")
        )
    except asyncio.TimeoutError:
        log(f"Connection to {target_ip}:{port} timed out after {config.timeout}s")
    except Exception as e:
        log(f"Failed to establish upstream bridge: {e}")
    finally:
        unix_writer.close()
        try:
            await unix_writer.wait_closed()
        except: pass
        log("Connection closed")

class MSysGitProxyServer:
    def __init__(self, upstream_path, unix_path, mode, config):
        self.upstream_path = upstream_path
        self.unix_path = unix_path
        self.mode = int(mode, 8)
        self.config = config

    async def start(self):
        if os.path.exists(self.unix_path):
            os.remove(self.unix_path)

        server = await asyncio.start_unix_server(
            lambda r, w: handle_unix_client(r, w, self.upstream_path, self.config),
            path=self.unix_path,
            backlog=self.config.listen_backlog
        )

        os.chmod(self.unix_path, self.mode)
        log(f"Listening on {self.unix_path}")

        async with server:
            await server.serve_forever()

def build_config():
    class ProxyAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            proxies = []
            for value in values:
                src, sep, dst = value.partition(':')
                if not sep:
                    raise parser.error(f'Invalid proxy pair "{value}". Use source:destination')
                proxies.append((src, dst))
            setattr(namespace, self.dest, proxies)

    parser = argparse.ArgumentParser(description='msysGit to Unix socket proxy (Asyncio Edition)')
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Enable verbose output.'
    )
    parser.add_argument(
        '--no-daemon',
        action='store_true',
        help='Run in foreground.'
    )
    parser.add_argument(
        '--ip-file',
        help='Path to file containing target IP (e.g. host-address.txt).'
    )
    parser.add_argument(
        '--downstream-buffer-size',
        default=8192,
        type=int
    )
    parser.add_argument(
        '--upstream-buffer-size',
        default=8192,
        type=int
    )
    parser.add_argument(
        '--listen-backlog',
        default=100,
        type=int
    )
    parser.add_argument(
        '--mode',
        default='0777'
    )
    parser.add_argument(
        '--pidfile',
        default='/var/run/wsl-keeagent-msysgit.pid',
        help='Where to write the PID file.'
    )
    parser.add_argument(
        '--timeout',
        default=60,
        type=int,
        help='Connection timeout in seconds.'
    )
    parser.add_argument(
        'proxies',
        nargs='+',
        action=ProxyAction,
        help='source:destination pairs'
    )
    return parser.parse_args()

def daemonize(pidfile):
    try:
        if os.fork() > 0: sys.exit(0)
    except OSError: sys.exit(1)
    os.setsid()
    os.umask(0)
    try:
        if os.fork() > 0: sys.exit(0)
    except OSError: sys.exit(1)
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, 'r') as si, open(os.devnull, 'a+') as so, open(os.devnull, 'a+') as se:
        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())
    with open(pidfile, 'w') as f:
        f.write(f"{os.getpid()}\n")

def cleanup(config):
    log("Cleaning up...")
    for _, unix_path in config.proxies:
        if os.path.exists(unix_path):
            os.remove(unix_path)
    if os.path.exists(config.pidfile):
        os.remove(config.pidfile)

async def main_loop(config):
    tasks = []
    for upstream, unix in config.proxies:
        server = MSysGitProxyServer(upstream, unix, config.mode, config)
        tasks.append(server.start())
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    config = build_config()
    VERBOSE = config.verbose

    if os.path.exists(config.pidfile):
        with open(config.pidfile, 'r') as f:
            try:
                content = f.read().strip()
                if content:
                    pid = int(content)
                    if pid_exists(pid):
                        print(f"Already running with PID {pid}", file=sys.stderr)
                        sys.exit(0)
            except (ValueError, OSError): pass
        cleanup(config)

    if not config.no_daemon:
        daemonize(config.pidfile)
    else:
        with open(config.pidfile, 'w') as f:
            f.write(f"{os.getpid()}\n")

    atexit.register(cleanup, config)
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    try:
        asyncio.run(main_loop(config))
    except KeyboardInterrupt:
        pass
