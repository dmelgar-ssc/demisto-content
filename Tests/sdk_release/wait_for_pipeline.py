import argparse
import json
from Tests.scripts.utils import logging_wrapper as logging
import sys
import time
import requests
from Tests.scripts.utils.log_util import install_logging


GITLAB_SERVER_URL = 'https://gitlab.xdr.pan.local'
TIMEOUT = 60 * 60 * 6  # 6 hours


def get_pipeline_status(pipeline_id, project_id, token):
    url = f'{GITLAB_SERVER_URL}/api/v4/projects/{project_id}/pipelines/{pipeline_id}/jobs'
    res = requests.get(url, headers={'Authorization': f'Bearer {token}'})
    if res.status_code != 200:
        logging.error(f'Failed to get status of pipeline {pipeline_id}, request failed with error: {str(res.content)}')
        sys.exit(1)

    try:
        jobs_info = json.loads(res.content)
        pipeline_status = jobs_info[0].get('pipeline', {}).get('status')

    except Exception as e:
        logging.error(f'Unable to parse pipeline status response: {e}')
        sys.exit(1)

    return pipeline_status


def get_pipeline_info(pipeline_id, project_id, token):
    url = f'{GITLAB_SERVER_URL}/api/v4/projects/{project_id}/pipelines/{pipeline_id}'
    res = requests.get(url, headers={'Authorization': f'Bearer {token}'})
    if res.status_code != 200:
        logging.error(f'Failed to get status of pipeline {pipeline_id}, request failed with error: {str(res.content)}')
        sys.exit(1)

    try:
        pipeline_info = json.loads(res.content)
    except Exception as e:
        logging.error(f'Unable to parse pipeline status response: {e}')
        sys.exit(1)

    return pipeline_info


def main():
    install_logging('wait_for_pipeline.log', logger=logging)

    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument('-g', '--gitlab-api-token', help='Gitlab api token')
    arg_parser.add_argument('-p', '--pipeline-id', help='Pipeline id')
    arg_parser.add_argument('-pid', '--project-id', help='Project id')

    args = arg_parser.parse_args()

    token = args.gitlab_api_token
    pipeline_id = args.pipeline_id
    project_id = args.project_id

    pipeline_status = 'running'  # pipeline status when start to run

    # initialize timer
    start = time.time()
    elapsed: float = 0

    while pipeline_status not in ['failed', 'success', 'canceled'] and elapsed < TIMEOUT:
        logging.info(f'Pipeline {pipeline_id} status is {pipeline_status}')
        pipeline_status = get_pipeline_status(pipeline_id, project_id, token)
        time.sleep(300)

        elapsed = time.time() - start

    if elapsed >= TIMEOUT:
        logging.critical(f'Timeout reached while waiting for the pipeline to complete, pipeline number: {pipeline_id}')
        sys.exit(1)

    pipeline_url = get_pipeline_info(pipeline_id, project_id, token).get('web_url')
    print(pipeline_url)
    logging.info(f'The pipeline has finished. See pipeline here: {pipeline_url}')


if __name__ == "__main__":
    main()
