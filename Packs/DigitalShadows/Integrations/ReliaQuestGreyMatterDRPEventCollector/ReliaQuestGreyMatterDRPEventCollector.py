import demistomock as demisto  # noqa: F401
from CommonServerPython import *  # noqa: F401

from CommonServerUserPython import *  # noqa

import urllib3
from typing import Dict, Any
from requests.exceptions import ConnectionError, Timeout

# Disable insecure warnings
urllib3.disable_warnings()


''' CONSTANTS '''

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # ISO8601 format
DEFAULT_MAX_FETCH = 200

''' CLIENT CLASS '''


class ReilaQuestClient(BaseClient):
    """Client class to interact with the service API

    This Client implements API calls, and does not contain any XSOAR logic.
    Should only do requests and return data.
    It inherits from BaseClient defined in CommonServer Python.
    Most calls use _http_request() that handles proxy, SSL verification, etc.
    For this  implementation, no special attributes defined
    """

    def __init__(self, url: str, account_id: str, username: str, password: str, verify_ssl: bool = False, proxy: bool = False):
        self.url = url
        self.account_id = account_id
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        super().__init__(base_url=url, verify=verify_ssl, proxy=proxy, auth=(username, password))

    @retry(times=5, exceptions=(ConnectionError, Timeout))
    def http_request(self, url_suffix: str, method: str = "GET", headers: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        try:
            return self._http_request(method, url_suffix=url_suffix, headers=headers or {"searchlight-account-id": self.account_id}, params=params)
        except DemistoException as error:
            if isinstance(error.exception, ConnectionError):
                # raise connection error to re-trigger the retry for temporary connection/timeout errors
                raise error.exception
            raise

    def list_triage_item_events(self, event_created_before: str | None = None, event_created_after: str | None = None, limit: int = 1000, events_num_after: int | None = None) -> List[Dict[str, Any]]:
        """
        Args:
                api docs:
                    Return events with an event-num greater than this value
                    Must be greater than or equal to 0.
            event_created_before (str): retrieve events occurred before a specific time (included), format:  YYYY-MM-DDThh:mm:ssTZD.
            event_created_after (str): retrieve events occurred after a specific time (included), format:  YYYY-MM-DDThh:mm:ssTZD.
            limit (int): the maximum number of events to retrieve
            events_num_after (int): used for pagination, can be retrieved from the "event-num" value from previous responses.
        """

        params: dict = {"limit": limit}
        if event_created_before:
            params["event-created-before"] = event_created_before
        if event_created_after:
            params["event-created-after"] = event_created_after
        if events_num_after:
            params["events-num-after"] = events_num_after

        events = self.http_request("/triage-item-events", params=params)

        if events:
            while len(events) < limit and "event-num" in events[-1]:
                params.update({"event-num-after": events[-1]["event-num"]})
                events.extend(self.http_request("/triage-item-events", params=params))

        return events

    def triage_items(self, triage_item_ids: list[str]) -> List[Dict[str, Any]]:
        """
        Args:
            triage_item_ids: a list of triage item IDs.
            from api:
                One or more triage item identifiers to resolve
                Must provide between 1 and 100 items.
        """
        return self.http_request("/triage-items", params={"id": triage_item_ids})

    def get_alerts_by_ids(self, alert_ids: list[str]) -> List[Dict[str, Any]]:
        """
        List of alerts was created from alert_id fields of /triage-items  response

        Args:
            alert_ids: List of alerts was created from alert_id fields of /triage-items  response
            from api:
                One or more alert identifiers to resolve
                Must provide between 1 and 100 items.
        """
        return self.http_request("/alerts", params={"id": alert_ids})

    def get_incident_ids(self, incident_ids: list[str]) -> List[Dict[str, Any]]:
        """
        List of alerts was created from incident-id fields of /triage-items response

        Args:
            incident_ids: a list of incident-IDs.
        """
        return self.http_request("/incidents", params={"id": incident_ids})

    def get_asset_ids(self, asset_ids: list[str]) -> List[Dict[str, Any]]:
        """
        Retrieve the Asset Information for the Alert or Incident

        Args:
            asset_ids: a list of asset-IDs.
        """
        return self.http_request("/assets", params={"id": asset_ids})


def test_module(client: ReilaQuestClient) -> str:
    """Tests API connectivity and authentication'

    Returning 'ok' indicates that the integration works like it is supposed to.
    Connection to the service is successful.
    Raises exceptions if something goes wrong.

    :type client: ``Client``
    :param Client: client to use

    :return: 'ok' if test passed, anything else will fail the test.
    :rtype: ``str``
    """
    client.list_triage_item_events(limit=1)
    return "ok"


def collect_event_ids_by_type(triage_item_ids: )


def fetch_events(client: ReilaQuestClient, last_run: Dict[str, Any], max_fetch: int = DEFAULT_MAX_FETCH):

    events = client.list_triage_item_events()
    triage_item_ids = [event.get("triage-item-id") for event in events]
    demisto.info(f"Fetched the following item IDs: {triage_item_ids}")

    triaged_items = client.triage_items(triage_item_ids)
    for item in triaged_items:
        triage_item = item.get("triage-item") or {}
        if triage_item.get("source")


    incident_ids = [(item.get("triage-item") or {}).get() for item in triaged_items]




''' MAIN FUNCTION '''


def main() -> None:
    """main function, parses params and runs command functions

    :return:
    :rtype:
    """
    params = demisto.params()
    url = params.get("url")
    account_id = params.get("account_id")
    max_fetch = arg_to_number(params.get("max_fetch_events")) or 200
    first_fetch = dateparser.parse(params.get("first_fetch_events"), settings={'TIMEZONE': 'UTC'}).strftime(DATE_FORMAT)
    credentials = params.get("credentials") or {}
    username = credentials.get("identifier")
    password = credentials.get("password")
    verify_ssl = not argToBoolean(password.get("insecure", True))
    proxy = argToBoolean(params.get("proxy", False))

    command = demisto.command()
    demisto.info(f'Command being called is {command}')
    try:

        client = ReilaQuestClient(url, account_id=account_id, username=username, password=password, verify_ssl=verify_ssl, proxy=proxy)
        if command == 'test-module':
            return_results(test_module(client))
        elif command == "fetch-events":
            pass
        elif command == "reila-quest-get-events":
            pass
        else:
            raise NotImplementedError(f'Command {command} is not implemented.')

    # Log exceptions and return errors
    except Exception as e:
        return_error(f'Failed to execute {command} command.\nError:\n{str(e)}')


''' ENTRY POINT '''


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
