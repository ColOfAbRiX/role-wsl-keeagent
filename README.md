# wsl-keeagent

Ansible role to configure KeeAgent integration in WSL2 with Windows SSH agent.

## Description

This role provides integration between WSL2 and KeeAgent (KeePassXC plugin) running on Windows.
It uses [wsl-ssh-agent](https://github.com/rupor-github/wsl-ssh-agent) to forward SSH agent
requests from WSL2 to the Windows host where KeeAgent is managing SSH keys.

Key capabilities include:

- Automatic download and installation of wsl-ssh-agent binary
- Systemd service management for the SSH agent forwarder
- Automatic SSH_AUTH_SOCK environment configuration
- WSL2-only support (for now)
- Ubuntu-only support (for now)

## Requirements

- **WSL2** (Windows Subsystem for Linux version 2)
- **Ubuntu** (20.04+, 22.04+, 24.04+)
- **KeePassXC** with KeeAgent plugin running on Windows
- **Windows OpenSSH authentication agent** running

## Role Variables

| Variable                    | Default                          | Description                                                     |
| :---                        | :---                             | :---                                                            |
| `wsl_keeagent_enabled`      | `false`                          | Enable or disable KeeAgent integration                          |
| `wsl_keeagent_version`      | `""` (latest)                    | Version of wsl-ssh-agent to install                            |
| `wsl_keeagent_bin_path`     | `/usr/local/bin/wsl-ssh-agent`   | Path to install the wsl-ssh-agent binary                       |
| `wsl_keeagent_socket`       | `/tmp/wsl-ssh-agent.sock`        | Socket path for SSH_AUTH_SOCK                                  |
| `wsl_keeagent_github_repo`  | `rupor-github/wsl-ssh-agent`     | GitHub repository for wsl-ssh-agent                            |
| `wsl_keeagent_service_name` | `wsl-keeagent`                   | Name of the systemd service                                    |

### Variables set by the role

The role sets the following fact for use in other roles:

| Variable                        | Description                                  |
| :---                            | :---                                         |
| `wsl_keeagent_ssh_agent_socket` | The socket path (same as `wsl_keeagent_socket`) |

## Dependencies

No dependencies for this role.

## Example Playbook

```yaml
- hosts: wsl_installations
  roles:
    - role: wsl-keeagent
      wsl_keeagent_enabled: true
```

## Setup on Windows

Before using this role, ensure the following on your Windows machine:

1. Install **KeePassXC** with KeeAgent plugin
2. Enable KeeAgent in KeePassXC settings and configure it to use a database entry
3. Ensure Windows OpenSSH Authentication Agent service is running:
   ```powershell
   Get-Service ssh-agent | Start-Service
   ```
4. Set Windows SSH_AUTH_SOCK:
   ```powershell
   [System.Environment]::SetEnvironmentVariable("SSH_AUTH_SOCK", "\\.\pipe\openssh-ssh-agent", "User")
   ```

## Usage

After the role is applied, SSH connections from WSL2 will automatically use keys stored in
KeePassXC. No additional configuration is needed for most use cases.

### Manual verification

To verify the integration is working:

```bash
# Check that the socket exists
ls -la /tmp/wsl-ssh-agent.sock

# Check that SSH_AUTH_SOCK is set
echo $SSH_AUTH_SOCK

# List keys available in the agent
ssh-add -l
```

## Troubleshooting

### Socket not created

- Verify the wsl-keeagent service is running: `sudo service wsl-keeagent status`
- Check service logs: `journalctl -u wsl-keeagent`
- Ensure Windows OpenSSH agent is running on Windows

### Keys not available

- Verify KeeAgent is enabled in KeePassXC
- Ensure your KeePassXC database is unlocked
- Check that the Windows SSH_AUTH_SOCK environment variable is set

### Permission issues

- The socket must be accessible by all users who need to use SSH
- If running as a different user, adjust `wsl_keeagent_service_user` in vars

## License

MIT

## Author Information

Fabrizio Colonna (@ColOfAbRiX)

## References

- [wsl-ssh-agent GitHub](https://github.com/rupor-github/wsl-ssh-agent)
- [KeeAgent integration guide](https://gist.github.com/strarsis/e533f4bca5ae158481bbe53185848d49)
- [WSL 2 Compatibility](https://github.com/rupor-github/wsl-ssh-agent#wsl-2-compatibility)
