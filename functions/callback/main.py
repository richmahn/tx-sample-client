# -*- coding: utf-8 -*-

# Method for receiving a callback from tX Manager, can do something here such as email the user

# Python libraries
import os
import tempfile
import boto3
import json
import requests

# Functions from Python libraries
from mimetypes import MimeTypes
from datetime import datetime

# Functions from unfoldingWord libraries
from general_tools.file_utils import unzip, write_file
from general_tools.url_utils import download_file, get_url


def handle(event, context):
    # Getting data from payload which is the JSON that was sent from tx-manager
    if 'data' not in event:
        raise Exception('"data" not in payload')
    data = event['data']

    if 'vars' in event and isinstance(event['vars'], dict):
        data.update(event['vars'])

    # Getting the bucket to where we will unzip the converted files for door43.org. It is different from
    # production and testing, thus it is an environment variable the API Gateway gives us
    if 'cdn_bucket' not in data:
        raise Exception('"cdn_bucket" was not in payload')
    cdn_bucket = data['cdn_bucket']

    if 'identifier' not in data or not data['identifier']:
        raise Exception('"identifier" not in payload')

    user, repo, commit = data['identifier'].split('/')

    s3_commit_key = 'u/{0}/{1}/{2}'.format(user, repo, commit)  # The identifier is how to know which username/repo/commit this callback goes to

    s3_resource = boto3.resource('s3')
    bucket = s3_resource.Bucket(cdn_bucket)

    # Download the ZIP file of the converted files
    converted_zip_url = data['output']
    converted_zip_file = os.path.join(tempfile.gettempdir(), converted_zip_url.rpartition('/')[2])
    try:
        print('Downloading converted zip file from {0}...'.format(converted_zip_url))
        if not os.path.isfile(converted_zip_file):
            download_file(converted_zip_url, converted_zip_file)
    finally:
        print('finished.')

    # Unzip the archive
    unzip_dir = tempfile.mkdtemp(prefix='unzip_')
    try:
        print('Unzipping {0}...'.format(converted_zip_file))
        unzip(converted_zip_file, unzip_dir)
    finally:
        print('finished.')

    # Upload all files to the cdn_bucket with the key of <user>/<repo_name>/<commit> of the repo
    mime = MimeTypes()
    for root, dirs, files in os.walk(unzip_dir):
        for f in sorted(files):
            path = os.path.join(root, f)
            key = s3_commit_key + path.replace(unzip_dir, '')
            mime_type = mime.guess_type(path)[0]
            if not mime_type:
                mime_type = "text/html"
            print('Uploading {0} to {1}, mime_type: {2}'.format(f, key, mime_type))
            bucket.upload_file(path, key, ExtraArgs={'ContentType': mime_type, 'CacheControl': str('public, max-age=5')})

    # Now download the existing build_log.json file, update it and upload it back to S3
    s3_file = s3_resource.Object(cdn_bucket, s3_commit_key+'/build_log.json')
    build_log_json = json.loads(s3_file.get()['Body'].read())

    build_log_json['started_at'] = data['started_at']
    build_log_json['ended_at'] = data['ended_at']
    build_log_json['success'] = data['success']
    build_log_json['status'] = data['status']

    if 'log' in data and data['log']:
        build_log_json['log'] = data['log']
    else:
        build_log_json['log'] = []

    if 'warnings' in data and data['warnings']:
        build_log_json['warnings'] = data['warnings']
    else:
        build_log_json['warnings'] = []

    if 'errors' in data and data['errors']:
        build_log_json['errors'] = data['errors']
    else:
        build_log_json['errors'] = []

    build_log_file = os.path.join(tempfile.gettempdir(), 'build_log_finished.json')
    write_file(build_log_file, build_log_json)
    bucket.upload_file(build_log_file, s3_commit_key+'/build_log.json', ExtraArgs={'ContentType': 'application/json', 'CacheControl': str('public, max-age=5')})
    print('Uploaded the following content from {0} to {1}/build_log.json'.format(build_log_file, s3_commit_key))
    print(build_log_json)

    # Now we update, or generate, the commits.json for the repo which has all the commits
    s3_repo_key = 'u/{0}/{1}'.format(user, repo)
    project_url = '{0}/{1}/project.json'.format(data['cdn_url'], s3_repo_key)

    # Download the project.json file for this repo (create it if doesn't exist) and update it
    project = {}
    print("Getting {0}...".format(project_url))
    try:
        project = json.loads(get_url(project_url))
    except Exception as e:
        print("FAILED: {0}".format(e.message))
    finally:
        print('finished.')

    project['user'] = user
    project['repo'] = repo
    project['repo_url'] = 'https://git.door43.org/{0}/{1}'.format(user, repo)
    
    item = {
        'id': commit,
        'created_at': data['created_at'],
        'status': data['status'],
        'success': data['success'],
        'started_at': None,
        'ended_at': None
    }
    if 'started_at' in data:
        item['started_at'] = data['started_at']
    if 'ended_at' in data:
        item['ended_at'] = data['ended_at']

    if 'commits' not in project:
        project['commits'] = []
    print("BEFORE APPEND:")
    print(project['commits'])
    project['commits'].append(item)
    print("AFTER APPEND:")
    print(project['commits'])

    project_file = os.path.join(tempfile.gettempdir(), 'project.json')
    write_file(project_file, project)
    bucket.upload_file(project_file, s3_repo_key + '/project.json',
                       ExtraArgs={'ContentType': 'application/json', 'CacheControl': str('public, max-age=5')})
    print('Uploaded the following content from {0} to {1}/project.json'.format(project_file, s3_repo_key))
    print(project)

    print('Finished deploying to cdn_bucket.')

    print('Triggering Door43 Deployer')
    url = '{0}/tx/deploy'.format(data['api_url'])
    headers = {"content-type": "application/json"}
    print('Making request to {0} with payload:'.format(url))
    print(data)
    response = requests.post(url, json=data, headers=headers)
    print('finished.')

