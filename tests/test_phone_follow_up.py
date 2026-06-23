from text2sql_runtime.conversation import contextualize_question
from text2sql_runtime.phone_follow_up import rewrite_phone_detail_follow_up


def test_rewrite_phone_detail_follow_up_from_count_answer():
    history = [
        {"role": "user", "content": "查询 15303369302这个手机号的居民"},
        {"role": "assistant", "content": "手机号为 15303369302 的居民共有 4 位。"},
    ]
    rewritten = rewrite_phone_detail_follow_up("给出居民的具体信息", history)

    assert rewritten == "查询15303369302手机号的居民详细信息"


def test_contextualize_phone_detail_follow_up():
    history = [
        {"role": "user", "content": "查询 15303369302这个手机号的居民"},
        {"role": "assistant", "content": "手机号为 15303369302 的居民共有 4 位。"},
    ]
    rewritten, log = contextualize_question("给出居民的具体信息", history)

    assert rewritten == "查询15303369302手机号的居民详细信息"
    assert log is not None
    assert log["rewriteReason"] == "phone_detail_follow_up"
