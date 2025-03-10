import time
import typing

import json
import warnings
import jsonpickle
import logging
import uuid
from dateutil import parser
from datetime import datetime
from typing import List, Dict, Text, Any, Type, Optional

from rasa.core import utils

if typing.TYPE_CHECKING:
    from rasa.core.trackers import DialogueStateTracker

logger = logging.getLogger(__name__)


def deserialise_events(serialized_events: List[Dict[Text, Any]]) -> List["Event"]:
    """Convert a list of dictionaries to a list of corresponding events.

    Example format:
        [{"event": "slot", "value": 5, "name": "my_slot"}]
    """

    deserialised = []

    for e in serialized_events:
        if "event" in e:
            event = Event.from_parameters(e)
            if event:
                deserialised.append(event)
            else:
                logger.warning(
                    f"Unable to parse event '{event}' while deserialising. The event"
                    " will be ignored."
                )

    return deserialised


def deserialise_entities(entities):
    if isinstance(entities, str):
        entities = json.loads(entities)

    return [e for e in entities if isinstance(e, dict)]


def md_format_message(text, intent, entities):
    from rasa.nlu.training_data.formats import MarkdownWriter, MarkdownReader

    message_from_md = MarkdownReader()._parse_training_example(text)
    deserialised_entities = deserialise_entities(entities)
    return MarkdownWriter()._generate_message_md(
        {
            "text": message_from_md.text,
            "intent": intent,
            "entities": deserialised_entities,
        }
    )


def first_key(d, default_key):
    if len(d) > 1:
        for k, v in d.items():
            if k != default_key:
                # we return the first key that is not the default key
                return k
    elif len(d) == 1:
        return list(d.keys())[0]
    else:
        return None


# noinspection PyProtectedMember
class Event:
    """Events describe everything that occurs in
    a conversation and tell the :class:`rasa.core.trackers.DialogueStateTracker`
    how to update its state."""

    type_name = "event"

    def __init__(
        self,
        timestamp: Optional[float] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ) -> None:
        self.timestamp = timestamp or time.time()
        self._metadata = metadata or {}

    @property
    def metadata(self) -> Dict[Text, Any]:
        # Needed for compatibility with Rasa versions <1.4.0. Previous versions
        # of Rasa serialized trackers using the pickle module. For the moment,
        # Rasa still supports loading these serialized trackers with pickle,
        # but will use JSON in any subsequent save operations. Versions of
        # trackers serialized with pickle won't include the `_metadata`
        # attribute in their events, so it is necessary to define this getter
        # in case the attribute does not exist. For more information see
        # CHANGELOG.rst.
        return getattr(self, "_metadata", {})

    def __ne__(self, other: Any) -> bool:
        # Not strictly necessary, but to avoid having both x==y and x!=y
        # True at the same time
        return not (self == other)

    def as_story_string(self) -> Text:
        raise NotImplementedError

    @staticmethod
    def from_story_string(
        event_name: Text,
        parameters: Dict[Text, Any],
        default: Optional[Type["Event"]] = None,
    ) -> Optional[List["Event"]]:
        event_class = Event.resolve_by_type(event_name, default)

        if not event_class:
            return None

        return event_class._from_story_string(parameters)

    @staticmethod
    def from_parameters(
        parameters: Dict[Text, Any], default: Optional[Type["Event"]] = None
    ) -> Optional["Event"]:

        event_name = parameters.get("event")
        if event_name is None:
            return None

        event_class: Type[Event] = Event.resolve_by_type(event_name, default)
        if not event_class:
            return None

        return event_class._from_parameters(parameters)

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List["Event"]]:
        """Called to convert a parsed story line into an event."""
        return [cls(parameters.get("timestamp"), parameters.get("metadata"))]

    def as_dict(self) -> Dict[Text, Any]:
        d = {"event": self.type_name, "timestamp": self.timestamp}

        if self.metadata:
            d["metadata"] = self.metadata

        return d

    @classmethod
    def _from_parameters(cls, parameters: Dict[Text, Any]) -> Optional["Event"]:
        """Called to convert a dictionary of parameters to a single event.

        By default uses the same implementation as the story line
        conversation ``_from_story_string``. But the subclass might
        decide to handle parameters differently if the parsed parameters
        don't origin from a story file."""

        result = cls._from_story_string(parameters)
        if len(result) > 1:
            logger.warning(
                f"Event from parameters called with parameters "
                f"for multiple events. This is not supported, "
                f"only the first event will be returned. "
                f"Parameters: {parameters}"
            )
        return result[0] if result else None

    @staticmethod
    def resolve_by_type(
        type_name: Text, default: Optional[Type["Event"]] = None
    ) -> Optional[Type["Event"]]:
        """Returns a slots class by its type name."""
        from rasa.core import utils

        for cls in utils.all_subclasses(Event):
            if cls.type_name == type_name:
                return cls
        if type_name == "topic":
            return None  # backwards compatibility to support old TopicSet evts
        elif default is not None:
            return default
        else:
            raise ValueError(f"Unknown event name '{type_name}'.")

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        pass


# noinspection PyProtectedMember
class UserUttered(Event):
    """The user has said something to the bot.

    As a side effect a new ``Turn`` will be created in the ``Tracker``."""

    type_name = "user"

    def __init__(
        self,
        text: Optional[Text] = None,
        intent=None,
        entities=None,
        parse_data: Optional[Dict[Text, Any]] = None,
        timestamp: Optional[int] = None,
        input_channel: Optional[Text] = None,
        message_id: Optional[Text] = None,
        metadata: Optional[Dict] = None,
    ):
        self.text = text
        self.intent = intent if intent else {}
        self.entities = entities if entities else []
        self.input_channel = input_channel
        self.message_id = message_id

        super().__init__(timestamp, metadata)

        if parse_data:
            self.parse_data = parse_data
        else:
            self.parse_data = {
                "intent": self.intent,
                "entities": self.entities,
                "text": text,
                "message_id": self.message_id,
                "metadata": self.metadata,
            }

    @staticmethod
    def _from_parse_data(
        text: Text,
        parse_data: Dict[Text, Any],
        timestamp: Optional[int] = None,
        input_channel: Optional[Text] = None,
        message_id: Optional[Text] = None,
        metadata: Optional[Dict] = None,
    ):
        return UserUttered(
            text,
            parse_data.get("intent"),
            parse_data.get("entities", []),
            parse_data,
            timestamp,
            input_channel,
            message_id,
            metadata,
        )

    def __hash__(self):
        return hash(
            (self.text, self.intent.get("name"), jsonpickle.encode(self.entities))
        )

    def __eq__(self, other):
        if not isinstance(other, UserUttered):
            return False
        else:
            return (
                self.text,
                self.intent.get("name"),
                [jsonpickle.encode(ent) for ent in self.entities],
            ) == (
                other.text,
                other.intent.get("name"),
                [jsonpickle.encode(ent) for ent in other.entities],
            )

    def __str__(self):
        return "UserUttered(text: {}, intent: {}, entities: {})".format(
            self.text, self.intent, self.entities
        )

    @staticmethod
    def empty():
        return UserUttered(None)

    def as_dict(self) -> Dict[Text, Any]:
        _dict = super().as_dict()
        _dict.update(
            {
                "text": self.text,
                "parse_data": self.parse_data,
                "input_channel": getattr(self, "input_channel", None),
                "message_id": getattr(self, "message_id", None),
                "metadata": self.metadata,
            }
        )
        return _dict

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List[Event]]:
        try:
            return [
                cls._from_parse_data(
                    parameters.get("text"),
                    parameters.get("parse_data"),
                    parameters.get("timestamp"),
                    parameters.get("input_channel"),
                    parameters.get("message_id"),
                    parameters.get("metadata"),
                )
            ]
        except KeyError as e:
            raise ValueError(f"Failed to parse bot uttered event. {e}")

    def as_story_string(self, e2e: bool = False) -> Text:
        if self.intent:
            if self.entities:
                ent_string = json.dumps(
                    {ent["entity"]: ent["value"] for ent in self.entities},
                    ensure_ascii=False,
                )
            else:
                ent_string = ""

            parse_string = "{intent}{entities}".format(
                intent=self.intent.get("name", ""), entities=ent_string
            )
            if e2e:
                message = md_format_message(self.text, self.intent, self.entities)
                return "{}: {}".format(self.intent.get("name"), message)
            else:
                return parse_string
        else:
            return self.text

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        tracker.latest_message = self
        tracker.clear_followup_action()


# noinspection PyProtectedMember
class BotUttered(Event):
    """The bot has said something to the user.

    This class is not used in the story training as it is contained in the

    ``ActionExecuted`` class. An entry is made in the ``Tracker``."""

    type_name = "bot"

    def __init__(self, text=None, data=None, metadata=None, timestamp=None):
        self.text = text
        self.data = data or {}
        super().__init__(timestamp, metadata)

    def __members(self):
        data_no_nones = utils.remove_none_values(self.data)
        meta_no_nones = utils.remove_none_values(self.metadata)
        return (
            self.text,
            jsonpickle.encode(data_no_nones),
            jsonpickle.encode(meta_no_nones),
        )

    def __hash__(self):
        return hash(self.__members())

    def __eq__(self, other):
        if not isinstance(other, BotUttered):
            return False
        else:
            return self.__members() == other.__members()

    def __str__(self):
        return "BotUttered(text: {}, data: {}, metadata: {})".format(
            self.text, json.dumps(self.data), json.dumps(self.metadata)
        )

    def __repr__(self):
        return "BotUttered('{}', {}, {}, {})".format(
            self.text, json.dumps(self.data), json.dumps(self.metadata), self.timestamp
        )

    def apply_to(self, tracker: "DialogueStateTracker") -> None:

        tracker.latest_bot_utterance = self

    def as_story_string(self):
        return None

    def message(self) -> Dict[Text, Any]:
        """Return the complete message as a dictionary."""

        m = self.data.copy()
        m["text"] = self.text
        m["timestamp"] = self.timestamp
        m.update(self.metadata)

        if m.get("image") == m.get("attachment"):
            # we need this as there is an oddity we introduced a while ago where
            # we automatically set the attachment to the image. to not break
            # any persisted events we kept that, but we need to make sure that
            # the message contains the image only once
            m["attachment"] = None

        return m

    @staticmethod
    def empty():
        return BotUttered()

    def as_dict(self):
        d = super().as_dict()
        d.update({"text": self.text, "data": self.data, "metadata": self.metadata})
        return d

    @classmethod
    def _from_parameters(cls, parameters):
        try:
            return BotUttered(
                parameters.get("text"),
                parameters.get("data"),
                parameters.get("metadata"),
                parameters.get("timestamp"),
            )
        except KeyError as e:
            raise ValueError(f"Failed to parse bot uttered event. {e}")


# noinspection PyProtectedMember
class SlotSet(Event):
    """The user has specified their preference for the value of a ``slot``.

    Every slot has a name and a value. This event can be used to set a
    value for a slot on a conversation.

    As a side effect the ``Tracker``'s slots will be updated so
    that ``tracker.slots[key]=value``."""

    type_name = "slot"

    def __init__(
        self,
        key: Text,
        value: Optional[Any] = None,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ) -> None:
        self.key = key
        self.value = value
        super().__init__(timestamp, metadata)

    def __str__(self):
        return f"SlotSet(key: {self.key}, value: {self.value})"

    def __hash__(self):
        return hash((self.key, jsonpickle.encode(self.value)))

    def __eq__(self, other):
        if not isinstance(other, SlotSet):
            return False
        else:
            return (self.key, self.value) == (other.key, other.value)

    def as_story_string(self):
        props = json.dumps({self.key: self.value}, ensure_ascii=False)
        return f"{self.type_name}{props}"

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List[Event]]:

        slots = []
        for slot_key, slot_val in parameters.items():
            slots.append(SlotSet(slot_key, slot_val))

        if slots:
            return slots
        else:
            return None

    def as_dict(self):
        d = super().as_dict()
        d.update({"name": self.key, "value": self.value})
        return d

    @classmethod
    def _from_parameters(cls, parameters):
        try:
            return SlotSet(
                parameters.get("name"),
                parameters.get("value"),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        except KeyError as e:
            raise ValueError(f"Failed to parse set slot event. {e}")

    def apply_to(self, tracker):
        tracker._set_slot(self.key, self.value)


# noinspection PyProtectedMember
class Restarted(Event):
    """Conversation should start over & history wiped.

    Instead of deleting all events, this event can be used to reset the
    trackers state (e.g. ignoring any past user messages & resetting all
    the slots)."""

    type_name = "restart"

    def __hash__(self):
        return hash(32143124312)

    def __eq__(self, other):
        return isinstance(other, Restarted)

    def __str__(self):
        return "Restarted()"

    def as_story_string(self):
        return self.type_name

    def apply_to(self, tracker):
        from rasa.core.actions.action import (  # pytype: disable=pyi-error
            ACTION_LISTEN_NAME,
        )

        tracker._reset()
        tracker.trigger_followup_action(ACTION_LISTEN_NAME)


# noinspection PyProtectedMember
class UserUtteranceReverted(Event):
    """Bot reverts everything until before the most recent user message.

    The bot will revert all events after the latest `UserUttered`, this
    also means that the last event on the tracker is usually `action_listen`
    and the bot is waiting for a new user message."""

    type_name = "rewind"

    def __hash__(self):
        return hash(32143124315)

    def __eq__(self, other):
        return isinstance(other, UserUtteranceReverted)

    def __str__(self):
        return "UserUtteranceReverted()"

    def as_story_string(self):
        return self.type_name

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        tracker._reset()
        tracker.replay_events()


# noinspection PyProtectedMember
class AllSlotsReset(Event):
    """All Slots are reset to their initial values.

    If you want to keep the dialogue history and only want to reset the
    slots, you can use this event to set all the slots to their initial
    values."""

    type_name = "reset_slots"

    def __hash__(self):
        return hash(32143124316)

    def __eq__(self, other):
        return isinstance(other, AllSlotsReset)

    def __str__(self):
        return "AllSlotsReset()"

    def as_story_string(self):
        return self.type_name

    def apply_to(self, tracker):
        tracker._reset_slots()


# noinspection PyProtectedMember
class ReminderScheduled(Event):
    """ Allows asynchronous scheduling of action execution.

    As a side effect the message processor will schedule an action to be run
    at the trigger date."""

    type_name = "reminder"

    def __init__(
        self,
        action_name: Text,
        trigger_date_time: datetime,
        name: Optional[Text] = None,
        kill_on_user_message: bool = True,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ):
        """Creates the reminder

        Args:
            action_name: name of the action to be scheduled
            trigger_date_time: date at which the execution of the action
                should be triggered (either utc or with tz)
            name: id of the reminder. if there are multiple reminders with
                 the same id only the last will be run
            kill_on_user_message: ``True`` means a user message before the
                 trigger date will abort the reminder
            timestamp: creation date of the event
            metadata: optional event metadata
        """

        self.action_name = action_name
        self.trigger_date_time = trigger_date_time
        self.kill_on_user_message = kill_on_user_message
        self.name = name if name is not None else str(uuid.uuid1())
        super().__init__(timestamp, metadata)

    def __hash__(self):
        return hash(
            (
                self.action_name,
                self.trigger_date_time.isoformat(),
                self.kill_on_user_message,
                self.name,
            )
        )

    def __eq__(self, other):
        if not isinstance(other, ReminderScheduled):
            return False
        else:
            return self.name == other.name

    def __str__(self):
        return (
            "ReminderScheduled("
            "action: {}, trigger_date: {}, name: {}"
            ")".format(self.action_name, self.trigger_date_time, self.name)
        )

    def _data_obj(self):
        return {
            "action": self.action_name,
            "date_time": self.trigger_date_time.isoformat(),
            "name": self.name,
            "kill_on_user_msg": self.kill_on_user_message,
        }

    def as_story_string(self):
        props = json.dumps(self._data_obj())
        return f"{self.type_name}{props}"

    def as_dict(self):
        d = super().as_dict()
        d.update(self._data_obj())
        return d

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List[Event]]:

        trigger_date_time = parser.parse(parameters.get("date_time"))
        return [
            ReminderScheduled(
                parameters.get("action"),
                trigger_date_time,
                parameters.get("name", None),
                parameters.get("kill_on_user_msg", True),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        ]


# noinspection PyProtectedMember
class ReminderCancelled(Event):
    """Cancel all jobs with a specific name."""

    type_name = "cancel_reminder"

    def __init__(
        self,
        action_name: Text,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ):
        """
        Args:
            action_name: name of the scheduled action to be cancelled
            metadata: optional event metadata
        """

        self.action_name = action_name
        super().__init__(timestamp, metadata)

    def __hash__(self):
        return hash(self.action_name)

    def __eq__(self, other):
        return isinstance(other, ReminderCancelled)

    def __str__(self):
        return f"ReminderCancelled(action: {self.action_name})"

    def as_story_string(self):
        props = json.dumps({"action": self.action_name})
        return f"{self.type_name}{props}"

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List[Event]]:
        return [
            ReminderCancelled(
                parameters.get("action"),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        ]


# noinspection PyProtectedMember
class ActionReverted(Event):
    """Bot undoes its last action.

    The bot reverts everything until before the most recent action.
    This includes the action itself, as well as any events that
    action created, like set slot events - the bot will now
    predict a new action using the state before the most recent
    action."""

    type_name = "undo"

    def __hash__(self):
        return hash(32143124318)

    def __eq__(self, other):
        return isinstance(other, ActionReverted)

    def __str__(self):
        return "ActionReverted()"

    def as_story_string(self):
        return self.type_name

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        tracker._reset()
        tracker.replay_events()


# noinspection PyProtectedMember
class StoryExported(Event):
    """Story should get dumped to a file."""

    type_name = "export"

    def __init__(
        self,
        path: Optional[Text] = None,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ):
        self.path = path
        super().__init__(timestamp, metadata)

    def __hash__(self):
        return hash(32143124319)

    def __eq__(self, other):
        return isinstance(other, StoryExported)

    def __str__(self):
        return "StoryExported()"

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List[Event]]:
        return [
            StoryExported(
                parameters.get("path"),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        ]

    def as_story_string(self):
        return self.type_name

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        if self.path:
            tracker.export_stories_to_file(self.path)


# noinspection PyProtectedMember
class FollowupAction(Event):
    """Enqueue a followup action."""

    type_name = "followup"

    def __init__(
        self,
        name: Text,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ) -> None:
        self.action_name = name
        super().__init__(timestamp, metadata)

    def __hash__(self):
        return hash(self.action_name)

    def __eq__(self, other):
        if not isinstance(other, FollowupAction):
            return False
        else:
            return self.action_name == other.action_name

    def __str__(self):
        return f"FollowupAction(action: {self.action_name})"

    def as_story_string(self):
        props = json.dumps({"name": self.action_name})
        return f"{self.type_name}{props}"

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List[Event]]:

        return [
            FollowupAction(
                parameters.get("name"),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        ]

    def as_dict(self):
        d = super().as_dict()
        d.update({"name": self.action_name})
        return d

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        tracker.trigger_followup_action(self.action_name)


# noinspection PyProtectedMember
class ConversationPaused(Event):
    """Ignore messages from the user to let a human take over.

    As a side effect the ``Tracker``'s ``paused`` attribute will
    be set to ``True``. """

    type_name = "pause"

    def __hash__(self):
        return hash(32143124313)

    def __eq__(self, other):
        return isinstance(other, ConversationPaused)

    def __str__(self):
        return "ConversationPaused()"

    def as_story_string(self):
        return self.type_name

    def apply_to(self, tracker):
        tracker._paused = True


# noinspection PyProtectedMember
class ConversationResumed(Event):
    """Bot takes over conversation.

    Inverse of ``PauseConversation``. As a side effect the ``Tracker``'s
    ``paused`` attribute will be set to ``False``."""

    type_name = "resume"

    def __hash__(self):
        return hash(32143124314)

    def __eq__(self, other):
        return isinstance(other, ConversationResumed)

    def __str__(self):
        return "ConversationResumed()"

    def as_story_string(self):
        return self.type_name

    def apply_to(self, tracker):
        tracker._paused = False


# noinspection PyProtectedMember
class ActionExecuted(Event):
    """An operation describes an action taken + its result.

    It comprises an action and a list of events. operations will be appended
    to the latest ``Turn`` in the ``Tracker.turns``."""

    type_name = "action"

    def __init__(
        self,
        action_name: Text,
        policy: Optional[Text] = None,
        confidence: Optional[float] = None,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ):
        self.action_name = action_name
        self.policy = policy
        self.confidence = confidence
        self.unpredictable = False
        super().__init__(timestamp, metadata)

    def __str__(self):
        return "ActionExecuted(action: {}, policy: {}, confidence: {})".format(
            self.action_name, self.policy, self.confidence
        )

    def __hash__(self):
        return hash(self.action_name)

    def __eq__(self, other):
        if not isinstance(other, ActionExecuted):
            return False
        else:
            return self.action_name == other.action_name

    def as_story_string(self):
        return self.action_name

    @classmethod
    def _from_story_string(cls, parameters: Dict[Text, Any]) -> Optional[List[Event]]:

        return [
            ActionExecuted(
                parameters.get("name"),
                parameters.get("policy"),
                parameters.get("confidence"),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        ]

    def as_dict(self):
        d = super().as_dict()
        policy = None  # for backwards compatibility (persisted evemts)
        if hasattr(self, "policy"):
            policy = self.policy
        confidence = None
        if hasattr(self, "confidence"):
            confidence = self.confidence

        d.update({"name": self.action_name, "policy": policy, "confidence": confidence})
        return d

    def apply_to(self, tracker: "DialogueStateTracker") -> None:

        tracker.set_latest_action_name(self.action_name)
        tracker.clear_followup_action()


class AgentUttered(Event):
    """The agent has said something to the user.

    This class is not used in the story training as it is contained in the
    ``ActionExecuted`` class. An entry is made in the ``Tracker``."""

    type_name = "agent"

    def __init__(
        self,
        text: Optional[Text] = None,
        data: Optional[Any] = None,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ) -> None:
        self.text = text
        self.data = data
        super().__init__(timestamp, metadata)

    def __hash__(self):
        return hash((self.text, jsonpickle.encode(self.data)))

    def __eq__(self, other):
        if not isinstance(other, AgentUttered):
            return False
        else:
            return (self.text, jsonpickle.encode(self.data)) == (
                other.text,
                jsonpickle.encode(other.data),
            )

    def __str__(self):
        return "AgentUttered(text: {}, data: {})".format(
            self.text, json.dumps(self.data)
        )

    def apply_to(self, tracker: "DialogueStateTracker") -> None:

        pass

    def as_story_string(self):
        return None

    def as_dict(self):
        d = super().as_dict()
        d.update({"text": self.text, "data": self.data})
        return d

    @staticmethod
    def empty():
        return AgentUttered()

    @classmethod
    def _from_parameters(cls, parameters):
        try:
            return AgentUttered(
                parameters.get("text"),
                parameters.get("data"),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        except KeyError as e:
            raise ValueError(f"Failed to parse agent uttered event. {e}")


class Form(Event):
    """If `name` is not None: activates a form with `name`
        else deactivates active form
    """

    type_name = "form"

    def __init__(
        self,
        name: Optional[Text],
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ) -> None:
        self.name = name
        super().__init__(timestamp, metadata)

    def __str__(self):
        return f"Form({self.name})"

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if not isinstance(other, Form):
            return False
        else:
            return self.name == other.name

    def as_story_string(self):
        props = json.dumps({"name": self.name})
        return f"{self.type_name}{props}"

    @classmethod
    def _from_story_string(cls, parameters):
        """Called to convert a parsed story line into an event."""
        return [
            Form(
                parameters.get("name"),
                parameters.get("timestamp"),
                parameters.get("metadata"),
            )
        ]

    def as_dict(self):
        d = super().as_dict()
        d.update({"name": self.name})
        return d

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        tracker.change_form_to(self.name)


class FormValidation(Event):
    """Event added by FormPolicy to notify form action
        whether or not to validate the user input"""

    type_name = "form_validation"

    def __init__(
        self,
        validate: bool,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ) -> None:
        self.validate = validate
        super().__init__(timestamp, metadata)

    def __str__(self):
        return f"FormValidation({self.validate})"

    def __hash__(self):
        return hash(self.validate)

    def __eq__(self, other):
        return isinstance(other, FormValidation)

    def as_story_string(self):
        return None

    @classmethod
    def _from_parameters(cls, parameters):
        return FormValidation(
            parameters.get("validate"),
            parameters.get("timestamp"),
            parameters.get("metadata"),
        )

    def as_dict(self):
        d = super().as_dict()
        d.update({"validate": self.validate})
        return d

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        tracker.set_form_validation(self.validate)


class ActionExecutionRejected(Event):
    """Notify Core that the execution of the action has been rejected"""

    type_name = "action_execution_rejected"

    def __init__(
        self,
        action_name: Text,
        policy: Optional[Text] = None,
        confidence: Optional[float] = None,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[Text, Any]] = None,
    ) -> None:
        self.action_name = action_name
        self.policy = policy
        self.confidence = confidence
        super().__init__(timestamp, metadata)

    def __str__(self):
        return (
            "ActionExecutionRejected("
            "action: {}, policy: {}, confidence: {})"
            "".format(self.action_name, self.policy, self.confidence)
        )

    def __hash__(self):
        return hash(self.action_name)

    def __eq__(self, other):
        if not isinstance(other, ActionExecutionRejected):
            return False
        else:
            return self.action_name == other.action_name

    @classmethod
    def _from_parameters(cls, parameters):
        return ActionExecutionRejected(
            parameters.get("name"),
            parameters.get("policy"),
            parameters.get("confidence"),
            parameters.get("timestamp"),
            parameters.get("metadata"),
        )

    def as_story_string(self):
        return None

    def as_dict(self):
        d = super().as_dict()
        d.update(
            {
                "name": self.action_name,
                "policy": self.policy,
                "confidence": self.confidence,
            }
        )
        return d

    def apply_to(self, tracker: "DialogueStateTracker") -> None:
        tracker.reject_action(self.action_name)
