#!/usr/bin/env python3

"""
msysGit to Unix socket proxy
============================
This small script is intended to help use msysGit sockets with the new Windows Linux Subsystem (aka Bash for Windows).
It was specifically designed to pass SSH keys from the KeeAgent module of KeePass secret management application to the
ssh utility running in the WSL (it only works with Linux sockets). However, my guess is that it will have uses for other
applications as well.
In order to efficiently use it, I add it at the end of the ~/.bashrc file, like this:
    export SSH_AUTH_SOCK="/tmp/.ssh-auth-sock"
    ~/bin/msysgit2unix-socket.py /mnt/c/Users/User/keeagent.sock:$SSH_AUTH_SOCK
Command line usage: msysgit2unix-socket.py [-h] [--downstream-buffer-size N]
                                           [--upstream-buffer-size N] [--listen-backlog N]
                                           [--timeout N] [--pidfile FILE]
                                           source:destination [source:destination ...]
positional arguments:
  source:destination    A pair of a source msysGit and a destination Unix sockets.

options:
  -h, --help            show this help message and exit
  -v, --verbose         Enable verbose output for debugging.
  --no-daemon           Run in foreground without daemonizing.
  --downstream-buffer-size N
                        Maximum number of bytes to read at a time from the Unix socket.
  --upstream-buffer-size N
                        Maximum number of bytes to read at a time from the msysGit socket.
  --listen-backlog N    Maximum number of simultaneous connections to the Unix socket.
  --mode MODE           File system permissions of the socket.
  --timeout N           Timeout.
  --pidfile FILE        Where to write the PID file.
See: https://gist.github.com/duebbert/4298b5f4eb7cc064b09e9d865dd490c9
"""

import argparse
import asyncore
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


# NOTE: Taken from http://stackoverflow.com/a/6940314
def PidExists(pid):
    """Check whether pid exists in the current process table.
    UNIX only.
    """
    if pid < 0:
        return False
    if pid == 0:
        # According to "man 2 kill" PID 0 refers to every process
        # in the process group of the calling process.
        # On certain systems 0 is a valid PID but we have no way
        # to know that in a portable fashion.
        raise ValueError('invalid PID 0')
    try:
        os.kill(pid, 0)
    except OSError as err:
        if err.errno == errno.ESRCH:
            # ESRCH == No such process
            return False
        elif err.errno == errno.EPERM:
            # EPERM clearly means there's a process to deny access to
            return True
        else:
            # According to "man 2 kill" possible error values are
            # (EINVAL, EPERM, ESRCH)
            raise
    else:
        return True


class UpstreamHandler(asyncore.dispatcher_with_send):
    """
    This class handles the connection to the TCP socket listening on localhost that makes the msysGit socket.
    """
    def __init__(self, downstream_dispatcher, upstream_path):
        asyncore.dispatcher.__init__(self)
        self.out_buffer = b''
        self.downstream_dispatcher = downstream_dispatcher
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        port = UpstreamHandler.load_tcp_port_from_msysgit_socket_file(upstream_path)
        log(f"Connecting to TCP port {port}")
        self.connect(('localhost', port))

    @staticmethod
    def load_tcp_port_from_msysgit_socket_file(path):
        log(f"Reading msysgit socket file: {path}")
        with open(path, 'r') as f:
            content = f.read()
            log(f"Socket file content: {content}")
            m = re.search(r'>(\d+)', content)
            if m:
                port = int(m.group(1))
                log(f"Extracted port: {port}")
                return port
            raise ValueError(f"Could not extract port from socket file: {content}")

    def handle_connect(self):
        log("TCP connection established")

    def handle_close(self):
        log("TCP connection closed")
        self.close()
        self.downstream_dispatcher.close()

    def handle_read(self):
        data = self.recv(config.upstream_buffer_size)
        if data:
            log(f"Received {len(data)} bytes from TCP")
            self.downstream_dispatcher.send(data)


class DownstreamHandler(asyncore.dispatcher_with_send):
    """
    This class handles the connections that are being accepted on the Unix socket.
    """
    def __init__(self, downstream_socket, upstream_path):
        asyncore.dispatcher.__init__(self, downstream_socket)
        self.out_buffer = b''
        log("Creating upstream handler")
        self.upstream_dispatcher = UpstreamHandler(self, upstream_path)

    def handle_close(self):
        log("Unix socket connection closed")
        self.close()
        self.upstream_dispatcher.close()

    def handle_read(self):
        data = self.recv(config.downstream_buffer_size)
        if data:
            log(f"Received {len(data)} bytes from Unix socket")
            self.upstream_dispatcher.send(data)


class MSysGit2UnixSocketServer(asyncore.dispatcher):
    """
    This is the "server" listening for connections on the Unix socket.
    """
    def __init__(self, upstream_socket_path, unix_socket_path, mode):
        asyncore.dispatcher.__init__(self)
        self.upstream_socket_path = upstream_socket_path
        log(f"Creating Unix socket server at {unix_socket_path}")
        self.create_socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.bind(unix_socket_path)
        self.listen(config.listen_backlog)
        self.mode = mode
        os.chmod(unix_socket_path, mode)
        log(f"Unix socket server listening at {unix_socket_path}")

    def handle_accept(self):
        pair = self.accept()
        if pair is not None:
            sock, addr = pair
            log("New connection accepted")
            DownstreamHandler(sock, self.upstream_socket_path)


def build_config():
    class ProxyAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            proxies = []
            for value in values:
                src_dst = value.partition(':')
                if src_dst[1] == '':
                    raise parser.error('Unable to parse sockets proxy pair "%s".' % value)
                proxies.append([src_dst[0], src_dst[2]])
            setattr(namespace, self.dest, proxies)

    parser = argparse.ArgumentParser(
        description='Transforms msysGit compatible sockets to Unix sockets for the Windows Linux Subsystem.')
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output for debugging.')
    parser.add_argument(
        '--no-daemon',
        action='store_true',
        help='Run in foreground without daemonizing.')
    parser.add_argument(
        '--downstream-buffer-size',
        default=8192,
        type=int,
        metavar='N',
        help='Maximum number of bytes to read at a time from the Unix socket.')
    parser.add_argument(
        '--upstream-buffer-size',
        default=8192,
        type=int,
        metavar='N',
        help='Maximum number of bytes to read at a time from the msysGit socket.')
    parser.add_argument(
        '--listen-backlog',
        default=100,
        type=int,
        metavar='N',
        help='Maximum number of simultaneous connections to the Unix socket.')
    parser.add_argument(
        '--mode',
        default='0777',
        help='File system permissions of the socket.'
    )
    parser.add_argument(
        '--timeout',
        default=60,
        type=int,
        help='Timeout.',
        metavar='N')
    parser.add_argument(
        '--pidfile',
        default='/var/run/wsl-keeagent-msysgit.pid',
        metavar='FILE',
        help='Where to write the PID file.')
    parser.add_argument(
        'proxies',
        nargs='+',
        action=ProxyAction,
        metavar='source:destination',
        help='A pair of a source msysGit and a destination Unix sockets.')
    return parser.parse_args()


def daemonize():
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit()
    except OSError:
        sys.stderr.write('Fork #1 failed.')
        sys.exit(1)

    os.chdir('/')
    os.setsid()
    os.umask(0)

    try:
        pid = os.fork()
        if pid > 0:
            sys.exit()
    except OSError:
        sys.stderr.write('Fork #2 failed.')
        sys.exit(1)

    sys.stdout.flush()
    sys.stderr.flush()

    si = open('/dev/null', 'r')
    so = open('/dev/null', 'a+')
    se = open('/dev/null', 'a+')
    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())

    pid = str(os.getpid())
    with open(config.pidfile, 'w+') as f:
        f.write('%s\n' % pid)
    log(f"Daemonized with PID {pid}")


def cleanup():
    try:
        for pair in config.proxies:
            if os.path.exists(pair[1]):
                os.remove(pair[1])
        if os.path.exists(config.pidfile):
            os.remove(config.pidfile)
    except Exception as e:
        sys.stderr.write('%s' % (e))


if __name__ == '__main__':
    config = build_config()

    VERBOSE = config.verbose

    log("Starting msysgit2unix-socket.py")

    if os.path.exists(config.pidfile):
        # Check if process is really running, if not run cleanup
        f = open(config.pidfile, 'r')
        pid = int(f.readline().strip())
        if PidExists(pid):
            log(f"Process already running with PID {pid}")
            sys.stderr.write('%s: Already running (or at least pidfile "%s" already exists).\n' % (sys.argv[0], config.pidfile))
            sys.exit(0)
        else:
            log(f"Stale PID file, cleaning up")
            cleanup()

    mode = int(config.mode, base=8)
    for pair in config.proxies:
        log(f"Creating server for {pair[0]} -> {pair[1]}")
        MSysGit2UnixSocketServer(pair[0], pair[1], mode)

    # Only daemonize if --no-daemon is not specified
    if not config.no_daemon:
        log("Daemonizing process...")
        daemonize()
    else:
        log("Running in foreground (no daemon)")
        # Write PID file for systemd tracking
        pid = str(os.getpid())
        with open(config.pidfile, 'w+') as f:
            f.write('%s\n' % pid)
        log(f"Written PID {pid} to {config.pidfile}")

    # Redundant cleanup :)
    atexit.register(cleanup)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    log("Starting asyncore loop")
    asyncore.loop(config.timeout, True)
    log("asyncore loop ended")
