from stt_vault.ai_content import (
    build_content_analysis_prompt,
    format_content_summary,
    parse_content_analysis,
)


def test_content_analysis_contract_keeps_content_fields_and_filters_candidates() -> None:
    transcript = [
        {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "We should ship Friday."},
        {"start": 5.0, "end": 8.0, "speaker": "SPEAKER_01", "text": "I approve the plan."},
    ]
    prompt = build_content_analysis_prompt(transcript)
    analysis = parse_content_analysis(
        '''{
          "content_summary": "The team agreed to ship on Friday.",
          "themes": ["release planning"],
          "conclusions": ["Friday is the target date"],
          "decisions": ["Ship on Friday"],
          "action_items": [{"action": "Prepare the release", "owner": "SPEAKER_00"}],
          "open_questions": ["Is QA complete?"],
          "speaker_candidates": [
            {"speaker": "SPEAKER_00", "name": "Maya Chen", "confidence": 0.97},
            {"speaker": "SPEAKER_01", "name": "Unknown", "confidence": 0.99},
            {"speaker": "SPEAKER_02", "name": "Jordan Lee", "confidence": 0.94}
          ]
        }''',
        minimum_speaker_confidence=0.95,
    )

    assert "[SPEAKER_00 00:00-00:04] We should ship Friday." in prompt
    assert "[SPEAKER_01 00:05-00:08] I approve the plan." in prompt
    assert analysis.speaker_names == {"SPEAKER_00": "Maya Chen"}
    assert format_content_summary(analysis) == (
        "The team agreed to ship on Friday.\n\n"
        "Themes\n- release planning\n\n"
        "Conclusions\n- Friday is the target date\n\n"
        "Decisions\n- Ship on Friday\n\n"
        "Action items\n- SPEAKER_00: Prepare the release\n\n"
        "Open questions\n- Is QA complete?"
    )


def test_content_analysis_rejects_invalid_json() -> None:
    try:
        parse_content_analysis("not json", minimum_speaker_confidence=0.95)
    except ValueError as exc:
        assert str(exc) == "AI response was not valid JSON"
    else:
        raise AssertionError("Expected invalid JSON to be rejected")
