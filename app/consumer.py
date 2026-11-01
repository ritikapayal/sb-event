import json
import logging
import os
import time
from collections.abc import Iterable, Mapping
from typing import Any

from azure.servicebus import ServiceBusClient
from azure.servicebus.exceptions import ServiceBusError

logger = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Required environment variable {name} is not set")
    return value


def _property_text(value: object) -> object:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _application_property(properties: Mapping, *names: str) -> object:
    normalized_properties = {
        str(_property_text(key))
        .casefold()
        .replace("_", "")
        .replace("-", ""): _property_text(value)
        for key, value in properties.items()
    }
    for name in names:
        normalized_name = name.casefold().replace("_", "").replace("-", "")
        value = normalized_properties.get(normalized_name)
        if value is not None:
            return value
    return None


def _decode_json_body(body: Any) -> Any:
    if isinstance(body, Mapping):
        return body
    if isinstance(body, (bytes, bytearray)):
        return json.loads(body)
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, Iterable):
        parts = list(body)
        if all(isinstance(part, (bytes, bytearray)) for part in parts):
            return json.loads(b"".join(parts))
        return parts
    return body


def consume_messages(connection_string: str, queue_name: str) -> None:
    with ServiceBusClient.from_connection_string(connection_string) as client:
        with client.get_queue_receiver(
            queue_name=queue_name,
            max_wait_time=5,
        ) as receiver:
            logger.info("Listening for messages on queue %s", queue_name)
            while True:
                messages = receiver.receive_messages(
                    max_message_count=10,
                    max_wait_time=5,
                )
                for message in messages:
                    try:
                        application_properties = message.application_properties or {}
                        try:
                            body = _decode_json_body(message.body)
                        except (UnicodeDecodeError, json.JSONDecodeError) as error:
                            logger.warning(
                                "Message id=%s body is not valid JSON: %s",
                                message.message_id,
                                error,
                            )
                            body = None

                        events = body if isinstance(body, list) else [body]
                        event_records = [
                            event for event in events if isinstance(event, Mapping)
                        ]
                        if not event_records:
                            event_records = [{}]

                        for event in event_records:
                            data = event.get("data")
                            data = data if isinstance(data, Mapping) else {}
                            event_type = (
                                event.get("eventType")
                                or event.get("type")
                                or _application_property(
                                    application_properties,
                                    "event_type",
                                    "eventType",
                                    "type",
                                )
                                or message.subject
                            )
                            logger.info(
                                "Received message id=%s event_id=%r topic=%r "
                                "subject=%r event_type=%r event_time=%r "
                                "url=%r content_type=%r content_length=%r "
                                "blob_type=%r correlation_id=%r",
                                message.message_id,
                                event.get("id"),
                                event.get("topic"),
                                event.get("subject") or message.subject,
                                event_type,
                                event.get("eventTime"),
                                data.get("url")
                                or _application_property(
                                    application_properties,
                                    "url",
                                    "uri",
                                ),
                                data.get("contentType") or message.content_type,
                                data.get("contentLength"),
                                data.get("blobType"),
                                message.correlation_id,
                            )
                        receiver.complete_message(message)
                    except ServiceBusError:
                        logger.exception(
                            "Could not process message id=%s; abandoning it",
                            message.message_id,
                        )
                        receiver.abandon_message(message)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    connection_string = _required_env("SERVICE_BUS_CONNECTION_STRING")
    queue_name = _required_env("SERVICE_BUS_QUEUE_NAME")

    while True:
        try:
            consume_messages(connection_string, queue_name)
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            return
        except ServiceBusError:
            logger.exception("Service Bus connection failed; retrying in 5 seconds")
            time.sleep(5)


if __name__ == "__main__":
    main()
