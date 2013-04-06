# -*- coding: us-ascii -*-
# vim:ts=4:sw=4:softtabstop=4:smarttab:expandtab
#

# Vision for data structure to be stored in cache:

# games: {
#     game1: {
#         players: {
#             player1: {
#                 hand: [...],
#                 wins: int,
#                 submitted = current submitted card,
#             },
#             player2 {...},
#             ...
#         },
#         current_black_card = None|int,
#         submissions = [dict of player submissions for the round],
#         round: int,
#         card_czar = 'player1',  # int index into 'players'
#         black_deck = [],
#         white_deck = [],
#     },
#     game2 {...},
#     ...
# }

# Contract between LobbyView and PlayerView:
#
# LobbyView will give PlayerView a named player and a game stored in cache.
# PlayerView will return to LobbyView any request that does not have those things.
# On the cache, 'players' will hav a mapping of ids to players.

import os
import json
import random
import hashlib
import urllib

from django.conf import settings
from django.views.generic import FormView, TemplateView
from django.core.urlresolvers import reverse
from django.shortcuts import redirect
from django.core.cache import cache
from forms import PlayerForm, GameForm, CzarForm
from game import Game
from pprint import pprint
import log
import uuid


# Grab data from the cards json and set global, unaltered decks.
with open(os.path.join(settings.PROJECT_ROOT, 'data/data.json')) as data:
    cards = json.loads(data.read())
    black_cards = cards['black_cards']
    white_cards = cards['white_cards']
    blank_marker = cards['blank']


def gravatar_robohash_url(email, size=50):
    """Generate url for RoboHash image  (gravatar first then robohash)"""
    text_to_hash = hashlib.md5(email.lower()).hexdigest()
    robohash_url = "http://robohash.org/%s?size=%dx%d&gravatar=hashed" % (text_to_hash, size, size)
    return robohash_url

def gravatar_url(email, size=50, default='monsterid'):
    """Generate url for Gravatar image
    email - email address
    default = default_image_url or default hash type
    """
    gravatar_url = "http://www.gravatar.com/avatar/" + hashlib.md5(email.lower()).hexdigest() + "?"
    if default:
        gravatar_url += urllib.urlencode({'d':default, 's':str(size)})
    else:
        gravatar_url += urllib.urlencode({'s':str(size)})
    return gravatar_url

avatar_url = gravatar_robohash_url
avatar_url = gravatar_url


class PlayerView(FormView):

    template_name = 'player.html'
    form_class = PlayerForm

    def __init__(self, *args, **kwargs):
        super(PlayerView, self).__init__(*args, **kwargs)

    def dispatch(self, request, *args, **kwargs):
        # Setup for game and player
        
        self.player_id = self.request.session.get('player_id')
        
        session_ids = cache.get('session_ids', {})
        session_details = session_ids.get(self.player_id, {})
        if not session_details:
            return redirect(reverse('lobby-view'))
        
        self.game_name = session_details['game']
        self.player_name = session_details['name']

        try:
            self.game_data = cache.get('games').get(self.game_name)
        except AttributeError:
            return redirect(reverse('lobby-view'))
        if not self.game_data:
            return redirect(reverse('lobby-view'))
        self.is_card_czar = self.game_data['card_czar'] == self.player_id
        log.logger.debug('id %r name %r game %r', self.player_id, self.player_name, self.game_name)

        self.player_data = self.game_data['players'].get(self.player_name)
        # Deal hand if player doesn't have one.
        if not self.player_data['hand']:
            self.player_data['hand'] = [
                self.game_data['white_deck'].pop() for x in xrange(10)
            ]

        # Deal black card if game doesn't have one.
        if self.game_data['current_black_card'] is None:
            self.game_data['current_black_card'] = self.deal_black_card()
        self.write_state()

        if self.is_card_czar:
            self.form_class = CzarForm
        return super(PlayerView, self).dispatch(request, *args, **kwargs)


    def get_success_url(self):
        return reverse('player-view')

    def get_context_data(self, *args, **kwargs):
        context = super(PlayerView, self).get_context_data(*args, **kwargs)
        context['players'] = self.game_data['players']
        last_round_winner = self.game_data.get('last_round_winner', '')
        context['last_round_winner'] = last_round_winner
        if last_round_winner:
            context['last_round_winner_avatar'] = self.game_data['players'][last_round_winner]['player_avatar']
        context['black_card'] = self.black_card.replace(blank_marker, '______')
        context['player_name'] = self.player_name
        context['player_avatar'] = self.game_data['players'][self.player_name]['player_avatar']
        context['game_name'] = self.game_name
        context['show_form'] = self.can_show_form()
        # Display filled-in answer if player has submitted.
        if self.game_data['submissions'] and not self.is_card_czar:
            player_submission = self.game_data['submissions'].get(self.player_id)
            context['filled_in_question'] = self.replace_blanks(player_submission)
        context['action'] = reverse('player-view')
        return context

    def get_form_kwargs(self):
        self.black_card = black_cards[self.game_data['current_black_card']]
        kwargs = super(PlayerView, self).get_form_kwargs()
        if self.is_card_czar:
            kwargs['cards'] = [(player_id, self.replace_blanks(self.game_data['submissions'][player_id])) for player_id in self.game_data['submissions']]
        else:
            kwargs['blanks'] = black_cards[self.game_data['current_black_card']].count(blank_marker) or 1
            kwargs['cards'] = tuple(
                (card, white_cards[card]) for card in self.player_data['hand']
            )
        return kwargs

    def form_valid(self, form):
        if self.is_card_czar:
            session_ids = cache.get('session_ids')
            winner = form.cleaned_data['card_selection']
            log.logger.debug(winner)
            winner_name = session_ids[uuid.UUID(winner)].get('name')
            self.reset(winner_name, uuid.UUID(winner))
            
        else:
            submitted = form.cleaned_data['card_selection']
            # The form returns unicode strings. We want ints in our list.
            self.game_data['submissions'][self.player_id] = [int(card) for card in submitted]
            for card in self.game_data['submissions'][self.player_id]:
                self.player_data['hand'].remove(card)
            log.logger.debug('%r', form.cleaned_data['card_selection'])
        self.write_state()
        log.logger.debug(cache.get('games'))
        return super(PlayerView, self).form_valid(form)

    def write_state(self):
        self.game_data['players'][self.player_name] = self.player_data
        games_dict = cache.get('games')
        games_dict[self.game_name] = self.game_data
        cache.set('games', games_dict)

    def can_show_form(self):
        flag = False
        # import pdb; pdb.set_trace()
        if self.game_data['card_czar'] == self.player_id:
            if not self.game_data['submissions']:
                flag = False
            elif len(self.game_data['submissions']) == len(self.game_data['players']) - 1:
                flag = True
            else:
                flag = False
        else:
            if self.player_id in self.game_data['submissions']:
                flag = False
            else:
                flag = True
        return flag

    def replace_blanks(self, white_card_num_list):
        card_text = self.black_card
        num_blanks = self.black_card.count(blank_marker)
        # assume num_blanks count is valid and len(white_card_num_list) == num_blanks
        if num_blanks == 0:
            card_num = white_card_num_list[0]
            white_text = white_cards[card_num]
            white_text = '<strong>' + white_text + '</strong>'
            card_text = card_text + ' ' + white_text
        else:
            for card_num in white_card_num_list:
                white_text = white_cards[card_num]
                white_text = white_text.rstrip('.')
                """We can't change the case of the first letter in case
                it is a real name :-( We'd need to consult a word list,
                to make that decision which is way too much effort at
                the moment."""
                white_text = '<strong>' + white_text + '</strong>'
                card_text = card_text.replace(blank_marker, white_text, 1)
        return card_text

    def reset(self, winner=None, winner_id=None):
        self.game_data['submissions'] = {}
        num_blanks = black_cards[self.game_data['current_black_card']].count(blank_marker)
        self.game_data['current_black_card'] = self.deal_black_card()
        self.game_data['players'][winner]['wins'] += 1
        self.game_data['card_czar'] = winner_id
        self.game_data['round'] += 1
        self.game_data['last_round_winner'] = winner
        
        if num_blanks == 0:
            num_blanks = 1
        for _ in xrange(num_blanks):
            for player_name in self.game_data['players']:
                if player_name != self.player_name:
                    self.game_data['players'][player_name]['hand'].append(self.game_data['white_deck'].pop())

    def deal_black_card(self):
        black_card = self.game_data['black_deck'].pop()
        if len(self.game_data['black_deck']) == 0:
            shuffled_black = range(len(black_cards))
            random.shuffle(shuffled_black)
            self.game_data['black_deck'] = shuffled_black
        return black_card



class LobbyView(FormView):

    template_name = 'lobby.html'
    form_class = GameForm

    def __init__(self, *args, **kwargs):
        self.game_list = cache.get('games')
        self.player_counter = cache.get('player_counter', 0)  # this doesn't really count players, it counts number of lobby views

    # def dispatch(self, request, *args, **kwargs):
        # return super(PlayerView, self).dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse('player-view')

    def get_context_data(self, *args, **kwargs):
        context = super(LobbyView, self).get_context_data(*args, **kwargs)
        context['show_form'] = True
        self.player_id = self.request.session.get('player_id')
        if not self.player_id:
            self.player_id = uuid.uuid1()
            self.request.session['player_id'] = self.player_id
        self.player_counter = cache.get('player_counter', 0) + 1
        cache.set('player_counter', self.player_counter)
        context['player_counter'] = self.player_counter

        return context

    def get_form_kwargs(self):
        kwargs = super(LobbyView, self).get_form_kwargs()
        if self.game_list:
            kwargs['game_list'] = [(game, game) for game in self.game_list.keys()]
        kwargs['player_counter'] = self.player_counter
        return kwargs

    def form_valid(self, form):
        self.player_id = self.request.session.get('player_id')
        player_name = form.cleaned_data['player_name']
        
        existing_game = True
        # Set the game properties in the cache
        game_name = form.cleaned_data['new_game']
        games = cache.get('games', {})
        if game_name:
            # Attempting to create a new game
            existing_game = games.get(game_name)
            if not existing_game:
                # really a new game
                new_game = self.create_game()
                new_game['players'][player_name] = self.create_player(player_name)
                new_game['card_czar'] = self.player_id
                games[form.cleaned_data['new_game']] = new_game
        if existing_game:
            if not game_name:
                game_name = form.cleaned_data.get('game_list')
            log.logger.debug('existing_game %r', (game_name, player_name,))
            if not games[game_name]['players'].get(player_name):
                games[game_name]['players'][player_name] = self.create_player(player_name)
            else:
                # FIXME
                raise NotImplementedError('joining with player names alreaady in same game causes problems')
        cache.set('games', games)

        # Set the player properties in the cache
        session_ids = cache.get('session_ids', {})
        session_details = session_ids.get(self.player_id, {})
        session_details['name'] = player_name
        session_details['game'] = game_name
        session_ids[self.player_id] = session_details
        cache.set('session_ids', session_ids)

        return super(LobbyView, self).form_valid(form)

    def create_game(self):
        log.logger.debug("New Game called")
        """Create shuffled decks
        uses built in random, it may be better to plug-in a better
        random init routine and/also consider using
        https://pypi.python.org/pypi/shuffle/

        Also take a look at http://code.google.com/p/gcge/
        """
        shuffled_white = range(len(white_cards))
        random.shuffle(shuffled_white)
        shuffled_black = range(len(black_cards))
        random.shuffle(shuffled_black)

        # Basic data object for a game. Eventually, this will be saved in cache.
        return {
            'players': {},
            'current_black_card': None,  # get a new one my shuffled_black.pop()
            'submissions': {},
            'round': 0,
            'card_czar': '',
            'white_deck': shuffled_white,
            'black_deck': shuffled_black,
            'mode': 'submitting',
        }

    def create_player(self, player_name):
        log.logger.debug("new player called")
        # Basic data obj for player. Eventually, this will be saved in cache.
        return {
            'hand': [],
            'wins': 0,
            'player_avatar': avatar_url(player_name),
        }
