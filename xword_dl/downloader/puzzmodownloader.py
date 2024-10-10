import re
import secrets

import dateparser

from .basedownloader import BaseDownloader
from ..util import join_bylines

class PuzzmoDownloader(BaseDownloader):
    command = 'pzm'
    outlet = 'Puzzmo'
    outlet_prefix = 'Puzzmo'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.temporary_user_id = secrets.token_urlsafe(21)
        self.session.headers.update({'Puzzmo-Gameplay-Id':
                                        self.temporary_user_id})

    def _get_slug_for_puzzle(self, puzzleSlug=None):
        query = """mutation PlayGameRedirectScreenMutation(
                    $gameSlug: String!
                    $puzzleSlug: String
                    $temporaryUserID: String
                    $partnerSlug: String
                  ) {
                    startPlayingGame(gameSlug: $gameSlug, puzzleSlug: $puzzleSlug, temporaryUserID: $temporaryUserID, partnerSlug: $partnerSlug) {
                      slug
                      id
                  }
                }"""

        variables = {'gameSlug': 'crossword',
                     'puzzleSlug': puzzleSlug,
                     'tempraryUserID': self.temporary_user_id,
                     'partnerSlug': None}

        operation_name = 'PlayGameRedirectScreenMutation'

        payload = {'operationName': operation_name,
                  'query': query,
                  'variables': variables}

        redirect_res = self.session.post('https://www.puzzmo.com/_api/prod/graphql?PlayGameRedirectScreenMutation', json=payload)

        slug = redirect_res.json()['data']['startPlayingGame']['slug']

        return f'https://www.puzzmo.com/play/crossword/{slug}'

    def find_latest(self):
        return self._get_slug_for_puzzle()

    def find_by_date(self, dt):
        query = """query TodayScreenQuery(
                     $day: String
                   ) {
                     todayPage(day: $day, applyAccessControls: false) {
                       daily {
                         puzzles {
                           puzzle {
                             slug
                             name
                             game {
                               slug
                             }
                           }
                         }
                       }
                     }
                   }"""
        self.date = dt
        variables = {"day": dt.strftime("%Y-%m-%d")}
        payload = {
            "operationName": "TodayScreenQuery",
            "query": query,
            "variables": variables
        }

        r = self.session.post("https://www.puzzmo.com/_api/prod/graphql?TodayScreenQuery", json=payload)
        puzzles = r.json()["data"]["todayPage"]["daily"]["puzzles"]
        xword = next(
            (
                puzzle
                for puzzle in puzzles
                if puzzle["puzzle"]["game"]["slug"] == "crossword"
            ),
            None,
        )
        if xword:
            return self._get_slug_for_puzzle(xword["puzzle"]["slug"])
        else:
            raise XWordDLException("Could not find a puzzle for date.")

    def find_solver(self, url):
        return url

    def fetch_data(self, solver_url):
        slug = solver_url.rsplit('/')[-1]
        query = """query PlayGameScreenQuery(
                      $slug: ID!
                    ) {
                      todaysDaily {
                        dayString
                        id
                      }
                      gamePlay(id: $slug, pingOwnerForMultiplayer: true) {
                        puzzle {
                          name
                          emoji
                          puzzle
                          author
                          authors {
                            username
                            usernameID
                            name
                            id
                          }
                        }
                      }
                    }"""

        variables = {'gameSlug': 'crossword',
                     'myUserStateID': self.temporary_user_id + ':userstate',
                     'partnerSlug': None,
                     'playerID': self.temporary_user_id + ':userstate',
                     'slug': slug}

        operation_name = 'PlayGameScreenQuery'

        payload = {'operationName': operation_name,
                   'query': query,
                   'variables': variables}

        res = self.session.post('https://www.puzzmo.com/_api/prod/graphql?PlayGameScreenQuery', json=payload)

        if not self.date:
            self.date = dateparser.parse(
                    res.json()['data']['todaysDaily']['dayString'])

        return res.json()['data']['gamePlay']['puzzle']

    def parse_xword(self, xw_data):
        self.puzzle.title = xw_data.get('name','')
        self.puzzle.author = join_bylines([a['name'] for a in xw_data['authors']])
        puzzle_lines = [l.strip() for l in xw_data['puzzle'].splitlines()]

        section = None
        blank_count = 2
        named_sections = False
        default_sections = ['metadata', 'grid', 'clues', 'notes']
        observed_height = 0
        observed_width = 0
        fill = ''
        solution = ''
        markup = b''
        clue_list = []

        for line in puzzle_lines:
            if not line:
                blank_count += 1
                continue
            else:
                if line.startswith('## '):
                    named_sections = True
                    section = line[3:].lower()
                    blank_count = 0
                    continue

                elif not named_sections and blank_count >= 2:
                    section = default_sections.pop(0)
                    blank_count = 0

            if section == 'metadata':
                if ':' in line:
                    k, v = line.split(':', 1)
                    k, v = k.strip().lower(), v.strip()

                # In practice, these fields (and the height and width) are
                # less reliable than the other API-provided fields, so we will
                # only fall back to them.

                    if k == 'title' and not self.puzzle.title:
                        self.puzzle.title = v
                    elif k == 'author' and not self.puzzle.author:
                        self.puzzle.author = v
                    elif k == 'copyright':
                        self.puzzle.copyright = v.strip(' ©')

            elif section == 'grid':
                if not observed_width:
                    observed_width = len(line)

                observed_height += 1

                for c in line:
                    if c.isalpha():
                        fill += '-'
                        solution += c.upper()
                    else:
                        fill += '.'
                        solution += '.'

            elif section == 'clues':
                if clue_parts := re.match(r'([AD])(\d{1,2})\.(.*)', line):
                    clue_list.append((clue_parts[1],
                                     int(clue_parts[2]),
                                     clue_parts[3]))
                else:
                    continue

            elif section == 'design':
                if 'style' in line or '{' in line:
                    continue
                else:
                    for c in line:
                        markup += b'\x00' if c in '#.' else b'\x80'


        self.puzzle.height = observed_height
        self.puzzle.width = observed_width
        self.puzzle.solution = solution
        self.puzzle.fill = fill

        if b'\x80' in markup:
            self.puzzle.extensions[b'GEXT'] = markup
            self.puzzle._extensions_order.append(b'GEXT')
            self.puzzle.markup()

        clue_list.sort(key=lambda c: (c[1], c[0]))

        self.puzzle.clues = [c[2].split(' ~ ')[0].strip() for c in clue_list]

        return self.puzzle
