from __future__ import annotations


def test_procrastinate_job_message_redacts_document_text() -> None:
    from knowledge_ingest.logging_setup import redact_content_fields

    secret = "Customer-only Notion body with a 'quoted value' and\nmultiple lines."
    call_string = (
        "ingest_graphiti_episode[42]("
        f"artifact_id='artifact-1', document_text={secret!r}, org_id='org-1')"
    )

    for event in (
        f"Starting job {call_string}",
        f"Job {call_string} ended with status: Success, lasted 1.234 s",
    ):
        event_dict = {
            "event": event,
            "job": {
                "task_kwargs": {"artifact_id": "artifact-1", "document_text": secret},
                "call_string": call_string,
            },
        }

        scrubbed = redact_content_fields(None, "info", event_dict)

        assert secret not in repr(scrubbed)
        assert scrubbed["job"]["task_kwargs"]["artifact_id"] == "artifact-1"
        assert scrubbed["job"]["task_kwargs"]["document_text"] == "<redacted>"
        assert "document_text=" in scrubbed["event"]
        assert "<redacted>" in scrubbed["event"]
