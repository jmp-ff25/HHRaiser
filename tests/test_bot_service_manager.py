from __future__ import annotations

import unittest

from hh_raiser.bot.service_manager import (
    CommandResult,
    ServiceCommandError,
    SystemdServiceManager,
)


class FakeRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []

    async def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout: float = 30,
    ) -> CommandResult:
        self.calls.append(arguments)
        return self.results.pop(0)


class SystemdServiceManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_user_service_snapshot_without_shell(self) -> None:
        runner = FakeRunner(
            [
                CommandResult(
                    return_code=0,
                    stdout=(
                        "ActiveState=active\nSubState=running\nMainPID=321\n"
                        "Description=HHRaiser instance main"
                    ),
                    stderr="",
                )
            ]
        )
        manager = SystemdServiceManager(user_mode=True, runner=runner)

        snapshot = await manager.snapshot("hhraiser@main.service")

        self.assertTrue(snapshot.is_active)
        self.assertEqual(snapshot.main_pid, 321)
        self.assertEqual(runner.calls[0][:3], ("systemctl", "--user", "show"))
        self.assertIn("hhraiser@main.service", runner.calls[0])

    async def test_start_uses_fixed_argument_vector(self) -> None:
        runner = FakeRunner([CommandResult(return_code=0, stdout="", stderr="")])
        manager = SystemdServiceManager(user_mode=True, runner=runner)

        await manager.start("hhraiser@main.service")

        self.assertEqual(
            runner.calls,
            [("systemctl", "--user", "start", "hhraiser@main.service")],
        )

    async def test_restart_uses_fixed_argument_vector(self) -> None:
        runner = FakeRunner([CommandResult(return_code=0, stdout="", stderr="")])
        manager = SystemdServiceManager(user_mode=True, runner=runner)

        await manager.restart("hhraiser@main.service")

        self.assertEqual(
            runner.calls,
            [("systemctl", "--user", "restart", "hhraiser@main.service")],
        )

    async def test_failed_command_has_readable_error(self) -> None:
        runner = FakeRunner([CommandResult(return_code=5, stdout="", stderr="Unit not found")])
        manager = SystemdServiceManager(user_mode=True, runner=runner)

        with self.assertRaisesRegex(ServiceCommandError, "Unit not found"):
            await manager.stop("hhraiser@missing.service")
