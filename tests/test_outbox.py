from entity.outbox import Outbox


def test_pushed_messages_drain_in_order_then_the_outbox_is_empty():
    outbox = Outbox()
    outbox.push("agent 1 needs you")
    outbox.push("agent 2 is ready for review")

    assert outbox.drain() == ["agent 1 needs you", "agent 2 is ready for review"]
    assert outbox.drain() == []  # fully consumed


def test_arrived_is_set_on_push_and_cleared_on_drain():
    outbox = Outbox()
    assert not outbox.arrived.is_set()  # nothing waiting yet

    outbox.push("something to say")
    assert outbox.arrived.is_set()  # a lull can now be interrupted to speak it

    outbox.drain()
    assert not outbox.arrived.is_set()  # spoken, so the signal goes quiet again


def test_news_carries_which_agent_it_is_about_while_still_being_the_message():
    # Which agent a queued message is about has to survive the queue. Worked back out of the text
    # it would be reading the label to find the thing - and two of the four kinds of news the
    # Entity queues do not carry the name in any fixed place at all.
    outbox = Outbox()
    outbox.push("fixer: the drive link is fixed", about="fixer")

    [news] = outbox.drain()

    assert news == "fixer: the drive link is fixed"  # still only the message, to everything else
    assert news.about == "fixer"


def test_empty_outbox_is_falsy_and_a_pushed_one_is_truthy():
    outbox = Outbox()
    assert not outbox

    outbox.push("word from an agent")
    assert outbox
