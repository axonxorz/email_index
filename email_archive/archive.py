#!/usr/bin/env python
import sys
import os
import email
import email.utils
import email.parser
import datetime
import hashlib
from gzip import open as gzip_open
import logging
import uuid

import redis

from .config import Configuration
from .fifo import FIFOQueue


logger = logging.getLogger(__name__)



def check_archived_domain(addresses, domains):
    """Check addresses to see if any of them fall in the archived domain list.
    This is lazy as a matched domain could appear in the local-part of the address
    and trigger a match"""
    if addresses is None: return
    addresses = addresses.lower()
    for address in addresses.split(','):
        for domain in domains:
            if domain in address:
                return True


def archive_message(message, priority=2):
    """Parse an email.Message object and archive it if eligible"""
    archived_domains = [x.lower() for x in Configuration.ARCHIVED_DOMAINS]
    do_archive = False

    if 'Message-ID' in message:
        message_id = message['Message-ID']

        for addresses in [message.get('To'), message.get('From'), message.get('CC'), message.get('BCC')]:
            if check_archived_domain(addresses, archived_domains):
                do_archive = True
    else:
        logger.debug('No Message-ID, duplicate checking unavailable')
        message_id = uuid.uuid4()
        return None

    if do_archive:
        conn = redis.StrictRedis.from_url(Configuration.REDIS.get('url'))
        queue = FIFOQueue(Configuration.REDIS['queue'], conn)
        archive_date = message['Date']
        if archive_date is not None:
            archive_date = email.utils.parsedate(archive_date)
            archive_date = datetime.datetime(*archive_date[:6])
        else:
            archive_date = datetime.datetime.now()

        minute = int(archive_date.strftime('%M'))
        minute = minute - (minute % 10)
        archive_path = os.path.join(Configuration.ARCHIVE_DIR,
                                    archive_date.strftime('%Y'),
                                    archive_date.strftime('%m'),
                                    archive_date.strftime('%d'),
                                    '{}{}'.format(archive_date.strftime('%H'), str(minute).zfill(2)))
        try:
            os.makedirs(archive_path)
        except OSError as e:
            # Ignore EEXIST
            if e.errno != 17:
                raise Exception('Unable to create directories')

        messagetime = archive_date.strftime('%H%M')
        hash_id = hashlib.sha256(message_id.encode('utf8')).hexdigest()
        archive_path = os.path.join(archive_path, messagetime + '-' + hash_id + '.eml.gz')
        logger.debug('Archiving to {}'.format(archive_path))
        with gzip_open(archive_path, 'w') as fd:
            fd.write(str(message).encode('utf8'))

        queue.push(archive_path.replace(Configuration.ARCHIVE_DIR, '').lstrip('/'), priority=priority)

        return archive_path


def main():
    """Process a message coming from stdin. Postfix delivery will send it this way"""
    str_message = sys.stdin.read()
    parser = email.parser.HeaderParser()
    message = parser.parsestr(str_message)
    archive_message(message)


if __name__ == '__main__':
    main()
