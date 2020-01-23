from typing import Dict, List, Union
import demistomock as demisto
from CommonServerPython import *
from CommonServerUserPython import *


special = ['n', 't', '\\', '"', '\'', '7', 'r']


def errors_handel(res: List) -> Union[None, str]:
    error_msg: Union[None, str] = None
    if res and isinstance(res, list) and isinstance(res[0].get('Contents'), dict):
        if 'data' not in res[0]['Contents']:
            error_msg = res[0].get('Contents')
        elif res[0]['Contents']['data'] is None:
            error_msg = "Incidents not found."

    else:
        error_msg = f'failed to get incidents from demisto got {res}'

    return error_msg


def is_valid_args(args: Dict):
    for _key, value in args.items():
        i = 0
        while i < len(value):
            if value[i] == '\\':
                if value[i + 1] not in special:
                    error_msg: str = f'Error while parsing the argument: "{_key}" ' \
                                     f'\nSucceeded parsing untill:\n- "{value[0:i]}"'
                    return_error(error_msg, DemistoException(error_msg.replace('\n', ' ')))
                    return False
                else:
                    i += 1
            i += 1
    return True


def search_incidents(args: Dict):
    if is_valid_args(args):
        res: List = demisto.executeCommand('getIncidents', args)
        error: Union[None, str] = errors_handel(res)
        if error is not None:
            return_error(error, DemistoException(error))
        else:
            data: Dict = res[0]['Contents']['data']
            context_entry: Dict = {'foundIncidents': data}
            headers: List[str] = ['id', 'name', 'severity', 'status', 'owner', 'created', 'closed']
            md: str = tableToMarkdown(name="Incidents found", t=data, headers=headers)
            return_outputs(md, context_entry, res)


def main():
    try:
        sys.exit(1)
    except SystemExit as e:
        print(e.code == 1)
        print(e.args == 1)
    args: Dict = demisto.args()
    search_incidents(args)


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
