from app.adapters.zetaris_exists import counts_as_exists


def test_http_400_msg_field_counts_as_exists():
    body = '{"msg":"A database named \\"PHOENIX_REAL\\" already exists."}'
    assert counts_as_exists(body, 400) is True


def test_error_message_and_raw_body_count_regardless_of_status():
    assert counts_as_exists('{"error":{"message":"container already exists"}}', 200) is True
    assert counts_as_exists("view already exist", 500) is True


def test_another_400_is_still_a_failure():
    assert counts_as_exists('{"msg":"syntax error near SELECT"}', 400) is False
    assert counts_as_exists("", 400) is False


def test_the_word_duplicate_alone_does_not_count():
    assert counts_as_exists('{"msg":"duplicate key"}', 400) is False
