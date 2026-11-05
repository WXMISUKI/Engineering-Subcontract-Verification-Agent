import pytest

from app.standalone.setup_journal import SetupJournal, SetupJournalError


def test_journal_prevents_blind_retry_after_uncertain_remote_write(tmp_path):
    path = tmp_path / 'setup.json'
    journal = SetupJournal(path)
    assert journal.begin('upload:sha256') is None
    with pytest.raises(SetupJournalError, match='未确认'):
        SetupJournal(path).begin('upload:sha256')
    journal.finish('upload:sha256', 'new-document-id')
    assert SetupJournal(path).begin('upload:sha256') == 'new-document-id'


def test_journal_rejects_corrupt_state(tmp_path):
    path = tmp_path / 'setup.json'
    path.write_text('invalid', encoding='utf-8')
    with pytest.raises(SetupJournalError):
        SetupJournal(path)


def test_setup_lock_rejects_concurrent_initializers_and_releases(tmp_path):
    from app.standalone.setup_journal import setup_lock

    lock = tmp_path / 'setup.lock'
    with setup_lock(lock):
        with pytest.raises(SetupJournalError, match='锁'):
            with setup_lock(lock):
                pass
    assert not lock.exists()
    with setup_lock(lock):
        assert lock.exists()
