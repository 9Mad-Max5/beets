# This file is part of beets.
# Copyright 2019, Rahul Ahuja.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# urllib.parse added because of the better encoding handling

"""Adds Deezer release and track search support to the autotagger
"""

import collections
import time

import requests
import unidecode

import html
import re

from beets import ui
from beets.autotag import AlbumInfo, TrackInfo
from beets.dbcore import types
from beets.library import DateType
from beets.plugins import BeetsPlugin, MetadataSourcePlugin
from beets.util.id_extractors import deezer_id_regex


class DeezerPlugin(MetadataSourcePlugin, BeetsPlugin):
    data_source = 'Deezer'

    item_types = {
        'deezer_track_rank': types.INTEGER,
        'deezer_track_id': types.INTEGER,
        'deezer_updated': DateType(),
    }

    # Base URLs for the Deezer API
    # Documentation: https://developers.deezer.com/api/
    search_url = 'https://api.deezer.com/search/'
    album_url = 'https://api.deezer.com/album/'
    track_url = 'https://api.deezer.com/track/'

    id_regex = deezer_id_regex

    def __init__(self):
        super().__init__()

    def commands(self):
        """Add beet UI commands to interact with Deezer."""
        deezer_update_cmd = ui.Subcommand(
            'deezerupdate', help=f'Update {self.data_source} rank')

        def func(lib, opts, args):
            items = lib.items(ui.decargs(args))
            self.deezerupdate(items, ui.should_write())

        deezer_update_cmd.func = func

        return [deezer_update_cmd]

    def album_for_id(self, album_id):
        """Fetch an album by its Deezer ID or URL and return an
        AlbumInfo object or None if the album is not found.

        :param album_id: Deezer ID or URL for the album.
        :type album_id: str
        :return: AlbumInfo object for album.
        :rtype: beets.autotag.hooks.AlbumInfo or None
        """
        deezer_id = self._get_id('album', album_id, self.id_regex)
        if deezer_id is None:
            return None

        album_data = requests.get(self.album_url + deezer_id).json()
        if 'error' in album_data:
            self._log.debug(f"Error fetching album {album_id}: "
                            f"{album_data['error']['message']}")
            return None
        contributors = album_data.get('contributors')
        if contributors is not None:
            artist, artist_id = self.get_artist(contributors)
        else:
            artist, artist_id = None, None

        release_date = album_data['release_date']
        date_parts = [int(part) for part in release_date.split('-')]
        num_date_parts = len(date_parts)

        if num_date_parts == 3:
            year, month, day = date_parts
        elif num_date_parts == 2:
            year, month = date_parts
            day = None
        elif num_date_parts == 1:
            year = date_parts[0]
            month = None
            day = None
        else:
            raise ui.UserError(
                "Invalid `release_date` returned "
                "by {} API: '{}'".format(self.data_source, release_date)
            )

        tracks_obj = requests.get(
            self.album_url + deezer_id + '/tracks'
        ).json()
        tracks_data = tracks_obj['data']
        if not tracks_data:
            return None
        while "next" in tracks_obj:
            tracks_obj = requests.get(tracks_obj['next']).json()
            tracks_data.extend(tracks_obj['data'])

        tracks = []
        medium_totals = collections.defaultdict(int)
        for i, track_data in enumerate(tracks_data, start=1):
            track = self._get_track(track_data)
            track.index = i
            medium_totals[track.medium] += 1
            tracks.append(track)
        for track in tracks:
            track.medium_total = medium_totals[track.medium]

        return AlbumInfo(
            album=album_data['title'],
            album_id=deezer_id,
            deezer_album_id=deezer_id,
            artist=artist,
            artist_credit=self.get_artist([album_data['artist']])[0],
            artist_id=artist_id,
            tracks=tracks,
            albumtype=album_data['record_type'],
            va=len(album_data['contributors']) == 1
            and artist.lower() == 'various artists',
            year=year,
            month=month,
            day=day,
            label=album_data['label'],
            mediums=max(medium_totals.keys()),
            data_source=self.data_source,
            data_url=album_data['link'],
            cover_art_url=album_data.get('cover_xl'),
        )

    def _get_track(self, track_data):
        """Convert a Deezer track object dict to a TrackInfo object.

        :param track_data: Deezer Track object dict
        :type track_data: dict
        :return: TrackInfo object for track
        :rtype: beets.autotag.hooks.TrackInfo
        """
        artist, artist_id = self.get_artist(
            track_data.get('contributors', [track_data['artist']])
        )
        return TrackInfo(
            title=track_data['title'],
            track_id=track_data['id'],
            deezer_track_id=track_data['id'],
            isrc=track_data.get('isrc'),
            artist=artist,
            artist_id=artist_id,
            length=track_data['duration'],
            index=track_data.get('track_position'),
            medium=track_data.get('disk_number'),
            deezer_track_rank=track_data.get('rank'),
            medium_index=track_data.get('track_position'),
            data_source=self.data_source,
            data_url=track_data['link'],
            deezer_updated=time.time(),
        )

    def track_for_id(self, track_id=None, track_data=None):
        """Fetch a track by its Deezer ID or URL and return a
        TrackInfo object or None if the track is not found.

        :param track_id: (Optional) Deezer ID or URL for the track. Either
            ``track_id`` or ``track_data`` must be provided.
        :type track_id: str
        :param track_data: (Optional) Simplified track object dict. May be
            provided instead of ``track_id`` to avoid unnecessary API calls.
        :type track_data: dict
        :return: TrackInfo object for track
        :rtype: beets.autotag.hooks.TrackInfo or None
        """
        if track_data is None:
            deezer_id = self._get_id('track', track_id, self.id_regex)
            if deezer_id is None:
                return None
            track_data = requests.get(self.track_url + deezer_id).json()
            if 'error' in track_data:
                self._log.debug(f"Error fetching track {track_id}: "
                                f"{track_data['error']['message']}")
                return None
        track = self._get_track(track_data)

        # Get album's tracks to set `track.index` (position on the entire
        # release) and `track.medium_total` (total number of tracks on
        # the track's disc).
        album_tracks_data = requests.get(
            self.album_url + str(track_data['album']['id']) + '/tracks'
        ).json()['data']
        medium_total = 0
        for i, track_data in enumerate(album_tracks_data, start=1):
            if track_data['disk_number'] == track.medium:
                medium_total += 1
                if track_data['id'] == track.track_id:
                    track.index = i
        track.medium_total = medium_total
        return track

    @staticmethod
    def _construct_search_query(filters=None, keywords='', unidec=True):
        """Construct a query string with the specified filters and keywords to
        be provided to the Deezer Search API
        (https://developers.deezer.com/api/search).

        :param filters: (Optional) Field filters to apply.
        :type filters: dict
        :param keywords: (Optional) Query keywords to use.
        :type keywords: str
        :return: Query string to be provided to the Search API.
        :rtype: str
        """
        query_components = [
            keywords,
            ' '.join(f'{k}:"{v}"' for k, v in filters.items()),
        ]
        query = ' '.join([q for q in query_components if q])
        if not isinstance(query, str):
            query = query.decode('utf8')

        if unidec == True:
            query = unidecode.unidecode(query)

        return query

    def manipulate_query(self, query, query_type):
        # The deezer API only handles HTML Unicode symols correctly
        stdquery = query
        #query = query.replace(" & ", " ").replace(" x ", " ")

        q = html.escape(query, quote=False).encode(
            'ascii', 'xmlcharrefreplace').decode()
        query = "q=" + q

        self._log.debug(
            f"Fallback 1st norm/unidecode searching {self.data_source} for '{query}'"
        )
        response = requests.get(
            self.search_url + query_type, params=query
        )

        self._log.debug(
            f"Searching with url: '{response.url}'"
        )
        response.raise_for_status()
        response_data = response.json().get('data', [])

        # To enlight the matching process strip possible anoying featurings
        # As less artists is less an issue but now usabale if artist field is there
        if len(response_data) == 0 and "artist:" in query:
            a = query.split("artist:")
            albumquery = a[0]
            artistquery = a[1]
            if " & " in artistquery:
                x = artistquery.split(" &amp; ")
                artistquery = x[0] + '"'

            if ", " in artistquery:
                x = artistquery.split(", ")
                artistquery = x[0] + '"'

            if " x " in artistquery:
                x = artistquery.split(" x ")
                artistquery = x[0] + '"'

            query = albumquery + "artist:" + artistquery

            self._log.debug(
                f"Fallback 2nd norm/unidecode searching {self.data_source} for '{query}'"
            )
            response = requests.get(
                self.search_url + query_type, params=query
            )

            self._log.debug(
                f"Seraching with url: '{response.url}'"
            )
            response.raise_for_status()
            response_data = response.json().get('data', [])

        # As in my case somehowe from one as an ep handled deezer handles it as an album
        # Strip ep from album string
        if len(response_data) == 0 and "artist:" in query:
            a = query.split("artist:")
            albumquery = a[0]
            artistquery = a[1]
            if " EP" in albumquery:
                albumquery = albumquery.replace(" EP", "")

            query = albumquery + "artist:" + artistquery

            self._log.debug(
                f"Fallback 3th norm/unidecode searching {self.data_source} for '{query}'"
            )
            response = requests.get(
                self.search_url + query_type, params=query
            )

            self._log.debug(
                f"Seraching with url: '{response.url}'"
            )
            response.raise_for_status()
            response_data = response.json().get('data', [])

        if len(response_data) == 0:
            query = re.sub(' [\(\[].*?[\)\]]', '', query)

            self._log.debug(
                f"Fallback 4th norm/unidecode searching {self.data_source} for '{query}'"
            )
            response = requests.get(
                self.search_url + query_type, params=query
            )

            self._log.debug(
                f"Seraching with url: '{response.url}'"
            )
            response.raise_for_status()
            response_data = response.json().get('data', [])

        return response_data

    def _search_api(self, query_type, filters=None, keywords=''):
        """Query the Deezer Search API for the specified ``keywords``, applying
        the provided ``filters``.

        :param query_type: The Deezer Search API method to use. Valid types
            are: 'album', 'artist', 'history', 'playlist', 'podcast',
            'radio', 'track', 'user', and 'track'.
        :type query_type: str
        :param filters: (Optional) Field filters to apply.
        :type filters: dict
        :param keywords: (Optional) Query keywords to use.
        :type keywords: str
        :return: JSON data for the class:`Response <Response>` object or None
            if no search results are returned.
        :rtype: dict or None
        """
        query = self._construct_search_query(
            keywords=keywords, filters=filters, unidec=True
        )
        
        query = query.replace("(","").replace(")","")

        if not query:
            return None
        self._log.debug(
            f"Searching {self.data_source} for '{query}'"
        )
        response = requests.get(
            self.search_url + query_type, params={'q': query}
        )

        self._log.debug(
            f"Seraching with url: '{response.url}'"
        )
        response.raise_for_status()
        response_data = response.json().get('data', [])

        if len(response_data) == 0:
            response_data = self.manipulate_query(query, query_type)

        # As my libary uses a lot of european namings unidecode causes a lot of trouble
        # To get a better api response shutoff unideocde while creating the query
        if len(response_data) == 0:
            query = self._construct_search_query(
                keywords=keywords, filters=filters, unidec=False
            )

            query = query.replace("(","").replace(")","")


            if not query:
                return None

            self._log.debug(
                f"Searching without unidecode {self.data_source} for '{query}'"
            )

            response = requests.get(
                self.search_url + query_type, params={'q': query}
            )

            self._log.debug(
                f"Seraching with url: '{response.url}'"
            )
            response.raise_for_status()
            response_data = response.json().get('data', [])

            if len(response_data) == 0:
                response_data = self.manipulate_query(query, query_type)

        self._log.debug(
            "Found {} result(s) from {} for '{}'",
            len(response_data),
            self.data_source,
            query,
        )
        return response_data

    def deezerupdate(self, items, write):
        """Obtain rank information from Deezer."""
        for index, item in enumerate(items, start=1):
            self._log.info('Processing {}/{} tracks - {} ',
                           index, len(items), item)
            try:
                deezer_track_id = item.deezer_track_id
            except AttributeError:
                self._log.debug('No deezer_track_id present for: {}', item)
                continue
            try:
                rank = requests.get(
                    f"{self.track_url}{deezer_track_id}").json().get('rank')
                self._log.debug('Deezer track: {} has {} rank',
                                deezer_track_id, rank)
            except Exception as e:
                self._log.debug('Invalid Deezer track_id: {}', e)
                continue
            item.deezer_track_rank = int(rank)
            item.store()
            item.deezer_updated = time.time()
            if write:
                item.try_write()
