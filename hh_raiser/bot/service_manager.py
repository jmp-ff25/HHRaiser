from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from hh_raiser.bot.models import ServiceSnapshot


@dataclass(frozen=True)
class CommandResult:
    return_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    async def run(self, arguments: tuple[str, ...], *, timeout: float = 30) -> CommandResult:
        """Run one fixed argument vector without invoking a shell."""


class SubprocessRunner:
    """Execute a command directly, preventing shell command injection."""

    async def run(self, arguments: tuple[str, ...], *, timeout: float = 30) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ServiceCommandError("Системная команда не завершилась вовремя.") from None
        return CommandResult(
            return_code=process.returncode or 0,
            stdout=stdout.decode(errors="replace").strip(),
            stderr=stderr.decode(errors="replace").strip(),
        )


class ServiceCommandError(RuntimeError):
    """Raised when systemd cannot inspect or control an allow-listed unit."""


class SystemdServiceManager:
    """Control explicit systemd units using argument vectors instead of a shell."""

    def __init__(
        self,
        *,
        user_mode: bool,
        runner: CommandRunner | None = None,
    ) -> None:
        self._prefix = ("systemctl", "--user") if user_mode else ("systemctl",)
        self._journal_prefix = ("journalctl", "--user") if user_mode else ("journalctl",)
        self._runner = runner or SubprocessRunner()

    async def snapshot(self, service_name: str) -> ServiceSnapshot:
        result = await self._runner.run(
            (
                *self._prefix,
                "show",
                service_name,
                "--no-pager",
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
                "--property=Description",
            )
        )
        if result.return_code != 0:
            raise ServiceCommandError(_command_error(result, service_name))
        values = _parse_properties(result.stdout)
        raw_pid = values.get("MainPID", "0")
        return ServiceSnapshot(
            active_state=values.get("ActiveState", "unknown"),
            sub_state=values.get("SubState", "unknown"),
            main_pid=int(raw_pid) if raw_pid.isdigit() and raw_pid != "0" else None,
            description=values.get("Description", ""),
        )

    async def start(self, service_name: str) -> None:
        await self._change_state("start", service_name)

    async def stop(self, service_name: str) -> None:
        await self._change_state("stop", service_name)

    async def recent_logs(self, service_name: str, *, lines: int) -> str:
        result = await self._runner.run(
            (
                *self._journal_prefix,
                "--unit",
                service_name,
                "--lines",
                str(lines),
                "--no-pager",
                "--output=short-iso",
            )
        )
        if result.return_code != 0:
            raise ServiceCommandError(_command_error(result, service_name))
        return result.stdout or "Для этого экземпляра пока нет записей журнала."

    async def _change_state(self, action: str, service_name: str) -> None:
        result = await self._runner.run((*self._prefix, action, service_name), timeout=45)
        if result.return_code != 0:
            raise ServiceCommandError(_command_error(result, service_name))


def _parse_properties(value: str) -> dict[str, str]:
    return {
        key: item
        for line in value.splitlines()
        if "=" in line
        for key, item in (line.split("=", maxsplit=1),)
    }


def _command_error(result: CommandResult, service_name: str) -> str:
    detail = result.stderr or result.stdout or f"код {result.return_code}"
    return f"Не удалось выполнить действие для {service_name}: {detail}"
