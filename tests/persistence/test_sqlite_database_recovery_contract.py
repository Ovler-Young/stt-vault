import ast
import re
from pathlib import Path

import pytest

from stt_vault.core.models.records import ErrorRecord, FindProviderWorkItem
from stt_vault.persistence.sqlite_database import SqliteDatabase

REPOSITORY_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "stt_vault"
PERSISTENCE_ROOT = SOURCE_ROOT / "persistence"
SQLITE_DATABASE_PATH = PERSISTENCE_ROOT / "sqlite_database.py"


def _local_provider_ledger(
    database: SqliteDatabase, *, asset_id: str, work_item_id: str
) -> tuple[object, object, object, object, object]:
    """Capture every public local-provider ledger surface recovery must preserve."""
    return (
        database.get_provider_invocation(work_item_id, 1),
        database.find_provider_work_item(
            FindProviderWorkItem(
                job_id=asset_id,
                asset_id=asset_id,
                role="diarization",
                provider_id="senko",
                image_digest="local",
                chunk_key="diarization",
                work_generation=1,
            )
        ),
        tuple(database.list_provider_invocation_transitions(work_item_id, 1)),
        database.get_provider_invocation(work_item_id, 2),
        tuple(
            event
            for event in database.list_events(asset_id)
            if event.payload is not None and event.payload.cause == f"senko:{work_item_id}:1"
        ),
    )


def _python_modules(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_sqlite_database_is_the_only_sql_implementation_and_split_entry_points_are_absent() -> None:
    """Keep the completed consolidation from retaining a parallel SQL boundary."""
    expected_modules = {"__init__.py", "sqlite_database.py"}
    actual_modules = {
        path.relative_to(PERSISTENCE_ROOT).as_posix() for path in _python_modules(PERSISTENCE_ROOT)
    }
    assert actual_modules == expected_modules

    forbidden_sql_owners: list[str] = []
    sql_string = re.compile(
        r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|BEGIN|COMMIT|ROLLBACK)\b",
        re.IGNORECASE,
    )
    for path in _python_modules(SOURCE_ROOT):
        if path == SQLITE_DATABASE_PATH:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "sqlite3" for alias in node.names
            ):
                forbidden_sql_owners.append(f"{path}:{node.lineno}: sqlite3 import")
            elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
                forbidden_sql_owners.append(f"{path}:{node.lineno}: sqlite3 import")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"execute", "executemany", "executescript", "cursor"}:
                    forbidden_sql_owners.append(f"{path}:{node.lineno}: {node.func.attr} call")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if sql_string.search(node.value):
                    forbidden_sql_owners.append(f"{path}:{node.lineno}: SQL literal")

    assert not forbidden_sql_owners, "\n".join(forbidden_sql_owners)


def test_recovery_protocol_uses_only_the_reserved_database_methods() -> None:
    """The recovery API has no compatibility entry points after consolidation."""
    from stt_vault.persistence.sqlite_database import SqliteDatabase

    assert callable(SqliteDatabase.claim_recoverable_jobs)
    assert callable(SqliteDatabase.complete_provider_recovery)
    for retired_name in (
        "recover_expired_jobs",
        "list_recoverable_provider_invocations",
        "recover_provider_invocation",
    ):
        assert not hasattr(SqliteDatabase, retired_name)


@pytest.mark.parametrize(
    "phase",
    ("claimed", "transcoding", "diarizing", "transcribing speech"),
)
def test_job_only_recovery_command_allows_terminal_provider_history(
    phase: str, tmp_path: Path
) -> None:
    """A job-only lease may follow terminal provider work without an active entry."""
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteProviderRecovery,
        JobOnlyRecoveryCommand,
        NewAsset,
    )
    from stt_vault.persistence.sqlite_database import SqliteDatabase

    database = SqliteDatabase(tmp_path / "recovery.sqlite3")
    try:
        database.initialize()
        database.create_asset(
            NewAsset(
                asset_id="asset-1",
                filename="clip.wav",
                media_type="audio",
                original_path=tmp_path / "clip.wav",
            )
        )
        database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
        database.update_stage(asset_id="asset-1", stage=phase)

        claims = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        )
        assert len(claims.commands) == 1
        command = claims.commands[0]
        assert isinstance(command, JobOnlyRecoveryCommand)
        assert command.phase == phase
        assert command.entries == ()

        completion = database.complete_provider_recovery(
            CompleteProviderRecovery(command=command, outcomes=(), now=11)
        )
        assert completion.requeued is True
    finally:
        database.close()


def test_provider_recovery_requires_the_complete_ordered_active_set_and_204_outcomes(
    tmp_path: Path,
) -> None:
    """Recovery has one conditional completion for every active invocation."""
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteProviderRecovery,
        NewAsset,
        PrepareProviderWorkItem,
        ProviderInvocationTransition,
        RecoveryProviderOutcome,
    )
    from stt_vault.persistence.sqlite_database import SqliteDatabase

    database = SqliteDatabase(tmp_path / "provider-recovery.sqlite3")
    try:
        database.initialize()
        database.create_asset(
            NewAsset(
                asset_id="asset-1",
                filename="clip.wav",
                media_type="audio",
                original_path=tmp_path / "clip.wav",
            )
        )
        database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
        database.update_stage(asset_id="asset-1", stage="transcribing speech")
        for work_item_id, state in (
            ("work-a", "prepared"),
            ("work-b", "sent"),
            ("work-c", "accepted"),
        ):
            prepared = database.prepare_provider_work_item(
                PrepareProviderWorkItem.for_transcription(
                    work_item_id=work_item_id,
                    job_id="asset-1",
                    asset_id="asset-1",
                    chunk_key=f"chunk:{work_item_id}",
                    run_attempt=1,
                    idempotency_key=f"00000000-0000-4000-8000-00000000000{work_item_id[-1]}",
                    request_hash="a" * 64,
                )
            )
            if state != "prepared":
                assert database.transition_provider_invocation(
                    ProviderInvocationTransition.sent(prepared)
                ).applied
            if state == "accepted":
                assert database.transition_provider_invocation(
                    ProviderInvocationTransition.accepted(prepared)
                ).applied

        claims = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        )
        command = claims.commands[0]
        assert command.kind == "provider_set"
        assert [(entry.work_item_id, entry.expected_state) for entry in command.entries] == [
            ("work-a", "prepared"),
            ("work-b", "sent"),
            ("work-c", "accepted"),
        ]

        with pytest.raises(ValueError, match="outcome"):
            database.complete_provider_recovery(
                CompleteProviderRecovery(command=command, outcomes=command.entries[:2], now=11)
            )
        with pytest.raises(ValueError, match="outcome"):
            database.complete_provider_recovery(
                CompleteProviderRecovery(
                    command=command,
                    outcomes=(
                        RecoveryProviderOutcome.prepared(command.entries[0]),
                        RecoveryProviderOutcome.cancelled(command.entries[1], http_status=204),
                        RecoveryProviderOutcome.cancelled(command.entries[1], http_status=204),
                    ),
                    now=11,
                )
            )

        retained = database.complete_provider_recovery(
            CompleteProviderRecovery(
                command=command,
                outcomes=(
                    RecoveryProviderOutcome.prepared(command.entries[0]),
                    RecoveryProviderOutcome.cancelled(command.entries[1], http_status=500),
                    RecoveryProviderOutcome.cancelled(command.entries[2], http_status=204),
                ),
                now=11,
            )
        )
        assert retained.requeued is False

        completed = database.complete_provider_recovery(
            CompleteProviderRecovery(
                command=command,
                outcomes=(
                    RecoveryProviderOutcome.prepared(command.entries[0]),
                    RecoveryProviderOutcome.cancelled(command.entries[1], http_status=204),
                    RecoveryProviderOutcome.cancelled(command.entries[2], http_status=204),
                ),
                now=11,
            )
        )
        assert completed.requeued is True
        from stt_vault.core.models.persistence_errors import StaleClaimError

        with pytest.raises(StaleClaimError):
            database.complete_provider_recovery(
                CompleteProviderRecovery(command=command, outcomes=(), now=11)
            )
    finally:
        database.close()


def test_job_only_recovery_ignores_terminal_provider_history(tmp_path: Path) -> None:
    """Completed, failed, and cancelled attempts do not make a set active."""
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        NewAsset,
        PrepareProviderWorkItem,
        ProviderInvocationTransition,
    )
    from stt_vault.persistence.sqlite_database import SqliteDatabase

    database = SqliteDatabase(tmp_path / "terminal-history.sqlite3")
    try:
        database.initialize()
        database.create_asset(
            NewAsset(
                asset_id="asset-1",
                filename="clip.wav",
                media_type="audio",
                original_path=tmp_path / "clip.wav",
            )
        )
        database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
        database.update_stage(asset_id="asset-1", stage="transcribing speech")
        for work_item_id, suffix, transition in (
            ("work-completed", "3", ProviderInvocationTransition.completed),
            ("work-failed", "4", ProviderInvocationTransition.failed),
            ("work-cancelled", "5", ProviderInvocationTransition.cancelled),
        ):
            prepared = database.prepare_provider_work_item(
                PrepareProviderWorkItem.for_transcription(
                    work_item_id=work_item_id,
                    job_id="asset-1",
                    asset_id="asset-1",
                    chunk_key=f"chunk:{work_item_id}",
                    run_attempt=1,
                    idempotency_key=f"00000000-0000-4000-8000-00000000000{suffix}",
                    request_hash="c" * 64,
                )
            )
            assert database.transition_provider_invocation(transition(prepared)).applied

        command = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        ).commands[0]
        assert command.kind == "job_only"
        assert command.entries == ()
    finally:
        database.close()


def test_recovery_completion_rejects_a_changed_active_membership(tmp_path: Path) -> None:
    """A claim cannot requeue after a concurrent active invocation appears."""
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteProviderRecovery,
        NewAsset,
        PrepareProviderWorkItem,
        RecoveryProviderOutcome,
    )
    from stt_vault.persistence.sqlite_database import SqliteDatabase

    database = SqliteDatabase(tmp_path / "membership-race.sqlite3")
    try:
        database.initialize()
        database.create_asset(
            NewAsset(
                asset_id="asset-1",
                filename="clip.wav",
                media_type="audio",
                original_path=tmp_path / "clip.wav",
            )
        )
        database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
        database.update_stage(asset_id="asset-1", stage="transcribing speech")
        database.prepare_provider_work_item(
            PrepareProviderWorkItem.for_transcription(
                work_item_id="work-a",
                job_id="asset-1",
                asset_id="asset-1",
                chunk_key="chunk:0",
                run_attempt=1,
                idempotency_key="00000000-0000-4000-8000-000000000001",
                request_hash="a" * 64,
            )
        )
        command = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        ).commands[0]
        database.prepare_provider_work_item(
            PrepareProviderWorkItem.for_transcription(
                work_item_id="work-b",
                job_id="asset-1",
                asset_id="asset-1",
                chunk_key="chunk:1",
                run_attempt=1,
                idempotency_key="00000000-0000-4000-8000-000000000002",
                request_hash="b" * 64,
            )
        )

        retained = database.complete_provider_recovery(
            CompleteProviderRecovery(
                command=command,
                outcomes=(RecoveryProviderOutcome.prepared(command.entries[0]),),
                now=11,
            )
        )
        assert retained.requeued is False
        assert retained.reservation_retained is True
    finally:
        database.close()


def test_expired_recovery_reservation_is_abandoned_before_a_new_claim(tmp_path: Path) -> None:
    """A restart waits for expiration and never reconstructs a recovery token."""
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        NewAsset,
    )
    from stt_vault.persistence.sqlite_database import SqliteDatabase

    database = SqliteDatabase(tmp_path / "expired-reservation.sqlite3")
    try:
        database.initialize()
        database.create_asset(
            NewAsset(
                asset_id="asset-1",
                filename="clip.wav",
                media_type="audio",
                original_path=tmp_path / "clip.wav",
            )
        )
        database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
        database.update_stage(asset_id="asset-1", stage="transcoding")

        first = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        ).commands[0]
        assert (
            database.claim_recoverable_jobs(
                ClaimRecoverableJobs(now=12, reservation_seconds=30)
            ).commands
            == ()
        )

        replacement = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=42, reservation_seconds=30)
        ).commands[0]
        assert replacement.token != first.token
        assert replacement.prior_run_attempt == first.prior_run_attempt
    finally:
        database.close()


def test_starting_claim_is_recovered_as_a_job_only_claim_without_a_stranded_reservation(
    tmp_path: Path,
) -> None:
    """The stage written by claim_next_job is a supported recovery phase."""
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteProviderRecovery,
        JobOnlyRecoveryCommand,
        NewAsset,
    )

    database = SqliteDatabase(tmp_path / "starting-recovery.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
    database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))

    recovery = database.claim_recoverable_jobs(ClaimRecoverableJobs(now=11, reservation_seconds=30))
    assert len(recovery.commands) == 1
    command = recovery.commands[0]
    assert isinstance(command, JobOnlyRecoveryCommand)
    assert command.phase == "claimed"
    assert database.complete_provider_recovery(
        CompleteProviderRecovery(command, (), now=11)
    ).requeued
    assert (
        database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=42, reservation_seconds=30)
        ).commands
        == ()
    )


def test_recovery_completion_requires_an_unexpired_matching_processing_claim_and_requeues_the_asset(
    tmp_path: Path,
) -> None:
    """Expired or changed recovery claims write neither a queue transition nor an audit event."""
    from stt_vault.core.models.persistence_errors import StaleClaimError
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteProviderRecovery,
        NewAsset,
    )

    database = SqliteDatabase(tmp_path / "recovery-predicates.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
    database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
    database.update_stage(asset_id="asset-1", stage="claimed")
    command = database.claim_recoverable_jobs(
        ClaimRecoverableJobs(now=11, reservation_seconds=1)
    ).commands[0]

    with pytest.raises(StaleClaimError):
        database.complete_provider_recovery(CompleteProviderRecovery(command, (), now=12))

    database = SqliteDatabase(tmp_path / "recovery-success.sqlite3")
    database.initialize()
    database.create_asset(NewAsset("asset-2", "clip.wav", "audio", tmp_path / "clip.wav"))
    database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
    database.update_stage(asset_id="asset-2", stage="claimed")
    command = database.claim_recoverable_jobs(
        ClaimRecoverableJobs(now=11, reservation_seconds=30)
    ).commands[0]
    assert database.complete_provider_recovery(
        CompleteProviderRecovery(command, (), now=11)
    ).requeued
    assert database.get_job("asset-2").status == "queued"
    assert database.get_asset("asset-2").status == "queued"
    events = database.list_events("asset-2")
    assert any(event.stage == "claimed" and event.message == "claimed" for event in events)
    recovery_events = [event for event in events if event.message == "Recovered expired job"]
    assert len(recovery_events) == 1
    recovery_event = recovery_events[0]
    assert recovery_event.stage == command.phase
    assert recovery_event.payload == ErrorRecord("recovery", "Recovered expired job")
    assert recovery_event.run_attempt == command.prior_run_attempt


def test_mixed_local_senko_and_sidecar_recovery_requires_ordered_role_aware_outcomes(
    tmp_path: Path,
) -> None:
    """A complete active set retains local and remote provider identities across recovery."""
    from stt_vault.core.models.persistence_errors import StaleClaimError
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteProviderRecovery,
        NewAsset,
        PrepareProviderWorkItem,
        ProviderInvocationTransition,
        RecoveryProviderOutcome,
    )

    database = SqliteDatabase(tmp_path / "mixed-local-remote-recovery.sqlite3")
    try:
        database.initialize()
        database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
        database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
        database.update_stage(asset_id="asset-1", stage="transcribing speech")
        local = database.prepare_provider_work_item(
            PrepareProviderWorkItem(
                work_item_id="diarization-work",
                job_id="asset-1",
                asset_id="asset-1",
                role="diarization",
                chunk_key="diarization",
                run_attempt=1,
                idempotency_key="00000000-0000-4000-8000-000000000001",
                request_hash="a" * 64,
                provider_id="senko",
                image_digest="local",
            )
        )
        remote = database.prepare_provider_work_item(
            PrepareProviderWorkItem.for_transcription(
                work_item_id="transcription-work",
                job_id="asset-1",
                asset_id="asset-1",
                chunk_key="chunk:0",
                run_attempt=1,
                idempotency_key="00000000-0000-4000-8000-000000000002",
                request_hash="b" * 64,
                provider_id="mod-whisper-cpu",
                image_digest="sha256:" + "b" * 64,
            )
        )
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.sent(local)
        ).applied
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.accepted(local)
        ).applied
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.sent(remote)
        ).applied

        command = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        ).commands[0]
        assert command.kind == "provider_set"
        assert [
            (entry.work_item_id, entry.expected_state, entry.role, entry.provider_id)
            for entry in command.entries
        ] == [
            ("diarization-work", "accepted", "diarization", "senko"),
            ("transcription-work", "sent", "transcription", "mod-whisper-cpu"),
        ]
        completed = database.complete_provider_recovery(
            CompleteProviderRecovery(
                command=command,
                outcomes=(
                    RecoveryProviderOutcome.abandoned(command.entries[0]),
                    RecoveryProviderOutcome.cancelled(command.entries[1], http_status=204),
                ),
                now=11,
            )
        )
        assert completed.requeued is True
        assert database.get_job("asset-1").status == "queued"
        assert database.get_asset("asset-1").status == "queued"
        local_invocation = database.get_provider_invocation("diarization-work", 1)
        assert local_invocation is not None
        assert local_invocation.state == "failed"
        assert local_invocation.error_category == "process_lost"
        assert local_invocation.cancelled_at is None
        assert local_invocation.cancellation_http_status is None
        assert database.get_provider_invocation("diarization-work", 2) is None
        local_transitions = database.list_provider_invocation_transitions("diarization-work", 1)
        assert [transition.to_state for transition in local_transitions] == [
            "prepared",
            "sent",
            "accepted",
            "failed",
        ]
        assert not tuple(
            event
            for event in database.list_events("asset-1")
            if event.payload is not None
            and event.payload.cause == "senko:diarization-work:1"
            and "cancel" in event.message.lower()
        )
        assert (
            database.get_provider_invocation("transcription-work", 1).cancellation_http_status
            == 204
        )

        with pytest.raises(StaleClaimError):
            database.complete_provider_recovery(
                CompleteProviderRecovery(command=command, outcomes=(), now=11)
            )
        assert database.get_provider_invocation("diarization-work", 1).state == "failed"
    finally:
        database.close()


def test_successful_local_recovery_reexecutes_diarization_with_a_new_work_generation(
    tmp_path: Path,
) -> None:
    """Recovery records the lost local process before re-executing its logical work."""
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteDiarizationProviderInvocation,
        CompleteProviderRecovery,
        CompleteTranscriptionProviderInvocation,
        DiarizationMetadata,
        FindProviderWorkItem,
        NewAsset,
        PrepareProviderWorkItem,
        ProviderInvocationTransition,
        RecoveryProviderOutcome,
        TranscriptSegment,
    )

    database = SqliteDatabase(tmp_path / "local-recovery-reexecution.sqlite3")
    try:
        database.initialize()
        database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
        database.claim_next_job(ClaimNextJob("worker-a", 1, now=10))
        database.update_stage(asset_id="asset-1", stage="diarizing")

        completed_transcription = database.prepare_provider_work_item(
            PrepareProviderWorkItem.for_transcription(
                work_item_id="completed-transcription",
                job_id="asset-1",
                asset_id="asset-1",
                chunk_key="chunk:0",
                run_attempt=1,
                idempotency_key="00000000-0000-4000-8000-000000000001",
                request_hash="a" * 64,
                provider_id="mod-whisper-cpu",
                image_digest="sha256:" + "a" * 64,
            )
        )
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.sent(completed_transcription)
        ).applied
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.accepted(completed_transcription)
        ).applied
        assert database.complete_transcription_and_provider_invocation(
            CompleteTranscriptionProviderInvocation(
                "completed-transcription",
                1,
                1,
                "asset-1",
                0,
                TranscriptSegment(0.0, 1.0, "SPEAKER_00", "finished"),
                1,
            )
        ).applied

        original = database.prepare_provider_work_item(
            PrepareProviderWorkItem(
                "original-diarization",
                "asset-1",
                "asset-1",
                "diarization",
                "asset",
                1,
                "00000000-0000-4000-8000-000000000002",
                "b" * 64,
                "senko",
                "local",
                1,
                "00000000-0000-4000-8000-000000000003",
            )
        )
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.sent(original)
        ).applied
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.accepted(original)
        ).applied
        recovery = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        ).commands[0]
        assert database.complete_provider_recovery(
            CompleteProviderRecovery(
                recovery,
                (RecoveryProviderOutcome.abandoned(recovery.entries[0]),),
                now=11,
            )
        ).requeued

        retained = database.get_provider_invocation(original.work_item_id, 1)
        assert retained is not None
        assert retained.state == "failed"
        assert retained.error_category == "process_lost"
        assert retained.cancelled_at is None
        assert retained.cancellation_http_status is None
        assert database.get_provider_invocation(original.work_item_id, 2) is None
        transitions = database.list_provider_invocation_transitions(original.work_item_id, 1)
        assert [transition.to_state for transition in transitions] == [
            "prepared",
            "sent",
            "accepted",
            "failed",
        ]
        assert transitions[-1].from_state == "accepted"
        assert sum(transition.to_state == "failed" for transition in transitions) == 1
        assert database.get_provider_invocation("completed-transcription", 1).state == "completed"
        assert database.get_provider_invocation("completed-transcription", 2) is None

        claim = database.claim_next_job(ClaimNextJob("worker-b", 30, now=12))
        assert claim is not None
        assert claim.run_attempt == 2
        replacement = database.prepare_provider_work_item(
            PrepareProviderWorkItem(
                "recovered-diarization",
                "asset-1",
                "asset-1",
                "diarization",
                "asset",
                claim.run_attempt,
                "00000000-0000-4000-8000-000000000004",
                "c" * 64,
                "senko",
                "local",
                claim.run_attempt,
                "00000000-0000-4000-8000-000000000005",
            )
        )
        assert replacement.work_item_id != original.work_item_id
        assert replacement.invocation_attempt == 1
        assert (
            database.find_provider_work_item(
                FindProviderWorkItem(
                    "asset-1", "asset-1", "diarization", "senko", "local", "asset", 1
                )
            ).work_item_id
            == original.work_item_id
        )
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.sent(replacement)
        ).applied
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.accepted(replacement)
        ).applied
        assert database.complete_diarization_and_provider_invocation(
            CompleteDiarizationProviderInvocation(
                replacement.work_item_id,
                replacement.invocation_attempt,
                claim.run_attempt,
                "asset-1",
                DiarizationMetadata("asset-1", tmp_path / "clip.wav", 1.0, {}, [], [], {}, None),
            )
        ).applied
        assert database.get_provider_invocation(replacement.work_item_id, 1).state == "completed"
    finally:
        database.close()


def test_local_senko_recovery_retains_failed_remote_cancellation_reservations_until_restart(
    tmp_path: Path,
) -> None:
    """A remote cancellation failure cannot partially abandon local work or requeue the job."""
    from stt_vault.core.models.persistence_errors import StaleClaimError
    from stt_vault.core.models.records import (
        ClaimNextJob,
        ClaimRecoverableJobs,
        CompleteProviderRecovery,
        NewAsset,
        PrepareProviderWorkItem,
        ProviderInvocationTransition,
        RecoveryProviderOutcome,
    )

    database = SqliteDatabase(tmp_path / "retained-local-remote-recovery.sqlite3")
    try:
        database.initialize()
        database.create_asset(NewAsset("asset-1", "clip.wav", "audio", tmp_path / "clip.wav"))
        database.claim_next_job(ClaimNextJob(claim_owner="worker-a", lease_seconds=1, now=10))
        database.update_stage(asset_id="asset-1", stage="transcribing speech")
        local = database.prepare_provider_work_item(
            PrepareProviderWorkItem(
                work_item_id="diarization-work",
                job_id="asset-1",
                asset_id="asset-1",
                role="diarization",
                chunk_key="diarization",
                run_attempt=1,
                idempotency_key="00000000-0000-4000-8000-000000000001",
                request_hash="a" * 64,
                provider_id="senko",
            )
        )
        remote = database.prepare_provider_work_item(
            PrepareProviderWorkItem.for_transcription(
                work_item_id="transcription-work",
                job_id="asset-1",
                asset_id="asset-1",
                chunk_key="chunk:0",
                run_attempt=1,
                idempotency_key="00000000-0000-4000-8000-000000000002",
                request_hash="b" * 64,
                provider_id="mod-whisper-cpu",
                image_digest="sha256:" + "b" * 64,
            )
        )
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.sent(local)
        ).applied
        assert database.transition_provider_invocation(
            ProviderInvocationTransition.sent(remote)
        ).applied
        command = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=11, reservation_seconds=30)
        ).commands[0]
        local_ledger = _local_provider_ledger(
            database, asset_id="asset-1", work_item_id="diarization-work"
        )

        retained = database.complete_provider_recovery(
            CompleteProviderRecovery(
                command=command,
                outcomes=(
                    RecoveryProviderOutcome.abandoned(command.entries[0]),
                    RecoveryProviderOutcome.cancelled(command.entries[1], http_status=503),
                ),
                now=11,
            )
        )
        assert retained.requeued is False
        assert retained.reservation_retained is True
        assert database.get_job("asset-1").status == "processing"
        assert database.get_provider_invocation("diarization-work", 1).state == "sent"
        assert database.get_provider_invocation("transcription-work", 1).state == "sent"
        assert (
            _local_provider_ledger(database, asset_id="asset-1", work_item_id="diarization-work")
            == local_ledger
        )
        assert (
            database.claim_recoverable_jobs(
                ClaimRecoverableJobs(now=12, reservation_seconds=30)
            ).commands
            == ()
        )

        replacement = database.claim_recoverable_jobs(
            ClaimRecoverableJobs(now=42, reservation_seconds=30)
        ).commands[0]
        assert replacement.token != command.token
        assert replacement.entries == command.entries
        with pytest.raises(StaleClaimError):
            database.complete_provider_recovery(
                CompleteProviderRecovery(command=command, outcomes=(), now=42)
            )
        assert (
            _local_provider_ledger(database, asset_id="asset-1", work_item_id="diarization-work")
            == local_ledger
        )
    finally:
        database.close()
