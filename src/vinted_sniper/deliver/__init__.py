"""Notification delivery: the outbox workers and the channels they send through."""

from vinted_sniper.deliver.base import Sender, SenderConfigError, SendResult
from vinted_sniper.deliver.dispatcher import Dispatcher

__all__ = ["Dispatcher", "SendResult", "Sender", "SenderConfigError"]
