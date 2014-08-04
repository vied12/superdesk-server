
from __future__ import absolute_import
from PIL import Image
from io import BytesIO
import os
import hashlib
import magic
import logging
from flask import json
import requests
import superdesk
from superdesk.file_meta.image import get_meta
from superdesk.file_meta.video import get_meta as video_meta

logger = logging.getLogger(__name__)


def hash_file(afile, hasher, blocksize=65536):
    buf = afile.read(blocksize)
    while len(buf) > 0:
        hasher.update(buf)
        buf = afile.read(blocksize)
    return hasher.hexdigest()


def get_file_name(file):
    return hash_file(file, hashlib.sha256())


def download_file_from_url(url):
    rv = requests.get(url)
    if rv.status_code not in (200, 201):
        payload = 'Failed to retrieve file from URL: %s' % url
        raise superdesk.SuperdeskError(payload=payload)

    mime = magic.from_buffer(rv.content, mime=True).decode('UTF-8')
    ext = mime.split('/')[1]
    name = 'stub.' + ext
    return BytesIO(rv.content), name, mime


def process_file_from_stream(content, filename=None, content_type=None):
    file_name = filename or content.filename
    content_type = content_type or content.content_type
    content = BytesIO(content.read())

    if 'application/' in content_type:
        content_type = magic.from_buffer(content.getvalue(), mime=True).decode('UTF-8')
        content.seek(0)

    file_type, ext = content_type.split('/')
    content, metadata = process_file(content, file_name, file_type)
    file_name = get_file_name(content)
    content.seek(0)
    metadata = encode_metadata(metadata)
    metadata.update({'length': json.dumps(len(content.getvalue()))})
    return file_name, content, content_type, metadata


def encode_metadata(metadata):
    return dict((k.lower(), json.dumps(v)) for k, v in metadata.items())


def decode_metadata(metadata):
    return dict((k.lower(), json.loads(v)) for k, v in metadata.items())


def process_file(content, file_name, type):
    if type == 'image':
        return process_image(content, file_name, type)
    if type in ('audio', 'video'):
        return process_video(content, file_name, type)
    return content, {}


def process_video(content, file_name, type):
    content.seek(0)
    meta = video_meta(content)
    content.seek(0)
    return content, meta


def process_image(content, file_name, type):
    content.seek(0)
    meta = get_meta(content)
    content.seek(0)
    return content, meta


def crop_image(content, file_name, cropping_data):
    if cropping_data:
        file_ext = os.path.splitext(file_name)[1][1:]
        if file_ext in ('JPG', 'jpg'):
            file_ext = 'jpeg'
        logger.debug('Opened image from stream, going to crop it s')
        content.seek(0)
        img = Image.open(content)
        cropped = img.crop(cropping_data)
        logger.debug('Cropped image from stream, going to save it')
        try:
            out = BytesIO()
            cropped.save(out, file_ext)
            out.seek(0)
            return (True, out)
        except Exception as io:
            logger.exception(io)
    return (False, content)