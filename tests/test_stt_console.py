from excephalon.stt_console import ConsoleSTT


def test_listen_returns_the_typed_line():
    stt = ConsoleSTT(read=lambda prompt: "hello entity")

    assert stt.listen() == "hello entity"


def test_eof_yields_a_farewell_so_the_loop_ends():
    def read(prompt):
        raise EOFError

    stt = ConsoleSTT(read=read, eof_utterance="goodbye entity")

    assert stt.listen() == "goodbye entity"
