import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import random
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
GUILD_ID = 1295468215681679481  # ⬅️ Replace this with your real Discord server ID

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)
@bot.event
@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)

    # Clear global commands (one-time clean)
    await bot.tree.sync()         # This refreshes global commands
    await bot.tree.clear_commands(guild=None)  
    await bot.tree.sync()         # Re-sync empty global commands

    # Now sync only your guild commands
    await bot.tree.sync(guild=guild)



# Fichier de sauvegarde des données
DATA_FILE = "blackjack_data.json"

# Stockage des données
active_duels = {}     # {message_id: {"creator": user, "mise": int, "players": [], "max_players": 4}}
active_games = {}     # {game_id: BlackjackGame object}
player_stats = {}     # {user_id: {"kamas_joues": int, "kamas_gagnes": int, "parties_gagnees": int, "parties_perdues": int}}

def get_user_stats(user_id):
    """Retourne les stats d'un joueur, initialise si nécessaire."""
    user_id_str = str(user_id)
    if user_id_str not in player_stats:
        player_stats[user_id_str] = {
            "kamas_joues": 0,
            "kamas_gagnes": 0,
            "parties_gagnees": 0,
            "parties_perdues": 0
        }
    return player_stats[user_id_str]

class BlackjackGame:
    def __init__(self, players, mise_par_joueur):
        self.players = players  # Liste des joueurs (discord.Member)
        self.mises = {player.id: mise_par_joueur for player in players}
        self.hands = {player.id: [] for player in players}
        self.scores = {player.id: 0 for player in players}
        self.stands = {player.id: False for player in players}  # Si le joueur a choisi de rester
        self.natural_blackjack = {player.id: False for player in players}  # True si blackjack naturel (2 cartes = 21)
        self.croupier_hand = []
        self.croupier_score = 0
        self.croupier_blackjack = False
        self.status = "en_cours"
        self.current_player_index = 0
        self.pot_total = mise_par_joueur * len(players)
        self.game_id = f"game_{random.randint(1000,9999)}"

    def distribuer_cartes_initiales(self):
        # Distribution initiale : 2 cartes par joueur
        for player in self.players:
            hand = [self.tirer_carte(), self.tirer_carte()]
            self.hands[player.id] = hand
            score = self.calculer_score(player.id)
            # Natural blackjack = 2 cartes qui totalisent 21 (As + 10)
            self.natural_blackjack[player.id] = (len(hand) == 2 and score == 21)
            if self.natural_blackjack[player.id]:
                # Le joueur naturel se met automatiquement en stand
                self.stands[player.id] = True

        # Le croupier tire 2 cartes (une face cachée)
        self.croupier_hand = [self.tirer_carte(), self.tirer_carte()]
        self.croupier_score = self.calculer_score_croupier()
        self.croupier_blackjack = (len(self.croupier_hand) == 2 and self.croupier_score == 21)
        # si croupier a blackjack naturel, il "stand" automatiquement (logique interne)

    def tirer_carte(self):
        # Retourne une valeur de carte correcte :
        # As = 1 (on traitera 11 dans calculer_score), 2-9, 10 pour 10/J/Q/K
        return random.choice([1,2,3,4,5,6,7,8,9,10,10,10,10])

    def calculer_score(self, player_id):
        main = self.hands[player_id]
        score = sum(main)
        # Gestion des As (1 ou 11)
        as_count = main.count(1)
        # Pour chaque As possible, on peut ajouter 10 (1 -> 11) si cela ne dépasse pas 21
        while as_count > 0 and score + 10 <= 21:
            score += 10
            as_count -= 1
        self.scores[player_id] = score
        return score

    def calculer_score_croupier(self):
        score = sum(self.croupier_hand)
        as_count = self.croupier_hand.count(1)
        while as_count > 0 and score + 10 <= 21:
            score += 10
            as_count -= 1
        self.croupier_score = score
        return score

    def joueur_actuel(self):
        """Retourne le joueur courant sans modifier l'index."""
        if self.current_player_index < len(self.players):
            return self.players[self.current_player_index]
        return None

    def joueur_suivant(self):
        """Passe au joueur suivant qui n'est pas en stand, retourne le joueur ou None."""
        self.current_player_index += 1
        while self.current_player_index < len(self.players) and self.stands[self.players[self.current_player_index].id]:
            self.current_player_index += 1
        return self.joueur_actuel()

    def tirer_carte_joueur(self, player_id):
        self.hands[player_id].append(self.tirer_carte())
        return self.calculer_score(player_id)

    def tous_joueurs_ont_joue(self):
        return all(self.stands[player.id] or self.scores[player.id] >= 21 for player in self.players)

    def jouer_croupier(self):
        # Le croupier tire jusqu'à avoir au moins 17 (prise en compte des As)
        while self.calculer_score_croupier() < 17:
            self.croupier_hand.append(self.tirer_carte())

    def determiner_gagnants(self):
        gagnants = []
        # mettre à jour le score du croupier (au cas où)
        self.calculer_score_croupier()

        for player in self.players:
            player_score = self.scores[player.id]

            # Le joueur perd automatiquement s'il dépasse 21
            if player_score > 21:
                continue

            player_natural = self.natural_blackjack.get(player.id, False)
            dealer_natural = self.croupier_blackjack

            # Cas où le croupier a busté
            if self.croupier_score > 21:
                # tout joueur restant <=21 gagne
                gagnants.append(player)
                continue

            # Si l'un a natural blackjack et l'autre non : natural gagne
            if player_natural and not dealer_natural:
                gagnants.append(player)
                continue
            if dealer_natural and not player_natural:
                # le joueur perd
                continue

            # Sinon comparer les scores
            if player_score > self.croupier_score:
                gagnants.append(player)
            # égalité => push (personne ne gagne)
            # si player_score == croupier_score -> ne rien faire

        return gagnants

class DuelButton(discord.ui.Button):
    def __init__(self, duel_message_id):
        super().__init__(label="Rejoindre le duel", style=discord.ButtonStyle.primary, emoji="🎮")
        self.duel_message_id = duel_message_id

    async def callback(self, interaction: discord.Interaction):
        if self.duel_message_id not in active_duels:
            await interaction.response.send_message("❌ Ce duel n'existe plus!", ephemeral=True)
            return

        duel_data = active_duels[self.duel_message_id]

        if interaction.user in duel_data["players"]:
            await interaction.response.send_message("❌ Vous êtes déjà dans ce duel!", ephemeral=True)
            return

        if interaction.user == duel_data["creator"]:
            await interaction.response.send_message("❌ Vous êtes le créateur de ce duel!", ephemeral=True)
            return

        if len(duel_data["players"]) >= duel_data["max_players"]:
            await interaction.response.send_message("❌ Ce duel est complet!", ephemeral=True)
            return

        duel_data["players"].append(interaction.user)

        embed = interaction.message.embeds[0]
        embed.clear_fields()

        embed.add_field(name="👤 Créateur", value=f"{duel_data['creator'].display_name}", inline=True)
        embed.add_field(name="💰 Mise", value=f"{duel_data['mise']:,} K", inline=True)
        embed.add_field(name="👥 Joueurs", value=f"{len(duel_data['players']) + 1}/{duel_data['max_players']}", inline=True)

        joueurs_liste = [f"• {duel_data['creator'].display_name} 👑"] + [f"• {player.display_name}" for player in duel_data["players"]]
        embed.add_field(
            name=f"🎮 Participants ({len(joueurs_liste)}/{duel_data['max_players']})",
            value="\n".join(joueurs_liste),
            inline=False
        )

        await interaction.message.edit(embed=embed)
        await interaction.response.send_message(f"✅ Vous avez rejoint le duel de {duel_data['creator'].display_name}!", ephemeral=True)

class DuelView(discord.ui.View):
    def __init__(self, duel_message_id):
        super().__init__(timeout=None)
        self.add_item(DuelButton(duel_message_id))

class GameButtonTirer(discord.ui.Button):
    def __init__(self, game_id):
        super().__init__(label="Tirer une carte", style=discord.ButtonStyle.primary, emoji="🃏")
        self.game_id = game_id

    def creer_embed_game(self, game, joueur_suivant):
        embed = discord.Embed(title="🎲 TABLE DE BLACKJACK", color=0xffff00)

        # Bloc croupier
        embed.add_field(
            name="🎯 Croupier",
            value=f"{[game.croupier_hand[0]] + ['❓']*(len(game.croupier_hand)-1)} (?)",
            inline=False
        )
        embed.add_field(name="-----", value="\u200b", inline=False)  # séparateur

        # Bloc joueurs
        for player in game.players:
            statut = "⏳ Joueur courant" if player == joueur_suivant else ""
            embed.add_field(
                name=f"👤 {player.display_name}",
                value=f"{game.hands[player.id]} ({game.scores[player.id]}) {statut}",
                inline=False
            )
            embed.add_field(name="-----", value="\u200b", inline=False)  # séparateur entre chaque joueur

        return embed

    async def callback(self, interaction: discord.Interaction):
        if self.game_id not in active_games:
            await interaction.response.send_message("❌ Cette partie n'existe plus!", ephemeral=True)
            return

        game = active_games[self.game_id]
        joueur_actuel = game.joueur_actuel()
        if interaction.user != joueur_actuel:
            await interaction.response.send_message("❌ Ce n'est pas votre tour!", ephemeral=True)
            return

        nouveau_score = game.tirer_carte_joueur(interaction.user.id)
        if nouveau_score >= 21:
            game.stands[interaction.user.id] = True
            joueur_suivant = game.joueur_suivant()
        else:
            joueur_suivant = game.joueur_actuel()

        await self.mettre_a_jour_interface(interaction, game, joueur_suivant)

    async def mettre_a_jour_interface(self, interaction, game, joueur_suivant):
        embed = self.creer_embed_game(game, joueur_suivant)
        view = GameView(self.game_id) if joueur_suivant else None

        if joueur_suivant:
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            game.jouer_croupier()
            await self.fin_de_partie(interaction, game)

    async def fin_de_partie(self, interaction, game):
        gagnants = game.determiner_gagnants()
        if gagnants:
            gain_par_joueur = int(game.pot_total * 0.95 / len(gagnants))
            gain_croupier = game.pot_total - (gain_par_joueur * len(gagnants))
        else:
            gain_par_joueur = 0
            gain_croupier = game.pot_total

        for player in game.players:
            stats = get_user_stats(player.id)
            stats["kamas_joues"] += game.mises[player.id]
            if player in gagnants:
                stats["kamas_gagnes"] += gain_par_joueur
                stats["parties_gagnees"] += 1
            else:
                stats["parties_perdues"] += 1

        embed = self.creer_embed_fin(game, gagnants, gain_par_joueur, gain_croupier)
        await interaction.response.edit_message(embed=embed, view=None)

        if self.game_id in active_games:
            del active_games[self.game_id]
        sauvegarder_donnees()

    # <-- CORRECTION: bien indentée dans la classe
    def creer_embed_fin(self, game, gagnants, gain_par_joueur, gain_croupier):
        embed = discord.Embed(title="🎲 TABLE DE BLACKJACK - FIN DE PARTIE", color=0x00ff00)

        # Mise totale
        embed.add_field(
            name="💰 Mise en jeu",
            value=f"{game.pot_total:,} K ({len(game.players)} joueurs)",
            inline=False
        )
        embed.add_field(name="-----", value="\u200b", inline=False)  # séparateur

        # Main finale du croupier
        embed.add_field(
            name="🎯 Croupier - Main finale",
            value=f"{game.croupier_hand} ({game.croupier_score})",
            inline=False
        )
        embed.add_field(name="-----", value="\u200b", inline=False)

        # Bloc des joueurs
        for player in game.players:
            if player in gagnants:
                statut = "🎉 Gagnant!"
            elif game.scores[player.id] > 21:
                statut = "💥 Dépassé!"
            elif game.scores[player.id] == game.croupier_score:
                statut = "🤝 Égalité"
            else:
                statut = "❌ Perdu"

            embed.add_field(
                name=f"👤 {player.display_name}",
                value=f"{game.hands[player.id]} ({game.scores[player.id]}) - {statut}",
                inline=False
            )

        embed.add_field(name="-----", value="\u200b", inline=False)

        # Bloc gagnants / croupier
        if gagnants:
            noms = ", ".join([g.display_name for g in gagnants])
            embed.add_field(
                name="🏆 Gagnant(s) 🎉",
                value=f"{noms} remportent **{gain_par_joueur:,} K** chacun !",
                inline=False
            )
            embed.add_field(
                name="💰 Croupier",
                value=f"Le croupier prend **{gain_croupier:,} K**",
                inline=False
            )
        else:
            embed.add_field(
                name="🤝 Résultat",
                value="Aucun gagnant cette fois !",
                inline=False
            )

        embed.add_field(name="-----", value="\u200b", inline=False)  # séparateur final
        return embed


class GameButtonRester(discord.ui.Button):
    def __init__(self, game_id):
        super().__init__(label="Rester", style=discord.ButtonStyle.secondary, emoji="✋")
        self.game_id = game_id

    async def callback(self, interaction: discord.Interaction):
        if self.game_id not in active_games:
            await interaction.response.send_message("❌ Cette partie n'existe plus!", ephemeral=True)
            return

        game = active_games[self.game_id]
        joueur_actuel = game.joueur_actuel()
        if interaction.user != joueur_actuel:
            await interaction.response.send_message("❌ Ce n'est pas votre tour!", ephemeral=True)
            return

        game.stands[interaction.user.id] = True
        joueur_suivant = game.joueur_suivant()
        button_tirer = GameButtonTirer(self.game_id)
        embed = button_tirer.creer_embed_game(game, joueur_suivant)
        view = GameView(self.game_id) if joueur_suivant else None

        if joueur_suivant:
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await button_tirer.fin_de_partie(interaction, game)

class GameView(discord.ui.View):
    def __init__(self, game_id):
        super().__init__(timeout=180)
        self.add_item(GameButtonTirer(game_id))
        self.add_item(GameButtonRester(game_id))


# ... (le reste du code reste identique: charger_donnees, sauvegarder_donnees, reset_stats_hebdo, on_ready, get_user_stats)

@bot.tree.command(name="duel", description="Créer un duel de blackjack avec une mise", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(mise="La mise en kamas que vous voulez jouer")
async def duel(interaction: discord.Interaction, mise: int):
    if mise <= 0:
        await interaction.response.send_message("❌ La mise doit être supérieure à 0!", ephemeral=True)
        return

    # ID des rôles à ping
    ROLE_CROUPIER_ID = 1297591998517088266
    ROLE_AUTRE_ID = 1295473800640466944
    roles_ping = f"<@&{ROLE_CROUPIER_ID}> <@&{ROLE_AUTRE_ID}>"

    embed = discord.Embed(
        title="🎲 Duel de Blackjack Multi-Joueurs",
        description=f"**{interaction.user.display_name}** a lancé un duel de blackjack !",
        color=0x00ff00
    )

    embed.add_field(name="👤 Créateur", value=f"{interaction.user.display_name}", inline=True)
    embed.add_field(name="💰 Mise", value=f"{mise:,} K", inline=True)
    embed.add_field(name="👥 Joueurs", value="1/4", inline=True)
    embed.add_field(name="🎮 Participants (1/4)", value=f"• {interaction.user.display_name} 👑", inline=False)
    embed.set_footer(text="Cliquez sur 'Rejoindre le duel' pour participer. Maximum 4 joueurs.")

    # Envoi avec ping autorisé pour les rôles
    allowed_mentions = discord.AllowedMentions(roles=True)
    message = await interaction.response.send_message(
        content=roles_ping,
        embed=embed,
        view=DuelView(interaction.id),
        allowed_mentions=allowed_mentions
    )

    active_duels[interaction.id] = {
        "creator": interaction.user,
        "mise": mise,
        "players": [],
        "max_players": 4,
        "message_id": interaction.id
    }

@bot.tree.command(name="start", description="Lancer le duel (Créateur uniquement)", guild=discord.Object(id=GUILD_ID))
async def start(interaction: discord.Interaction):
    duel_data = None
    duel_message_id = None

    for message_id, data in active_duels.items():
        if data["creator"] == interaction.user:
            duel_data = data
            duel_message_id = message_id
            break

    if not duel_data:
        await interaction.response.send_message("❌ Vous n'avez pas de duel en attente!", ephemeral=True)
        return

    total_players = len(duel_data["players"]) + 1
    if total_players < 2:
        await interaction.response.send_message("❌ Pas assez de joueurs! Attendez qu'au moins 1 joueur rejoigne.", ephemeral=True)
        return

    all_players = [duel_data["creator"]] + duel_data["players"]

    # Créer la partie de blackjack
    game = BlackjackGame(all_players, duel_data["mise"])
    game.distribuer_cartes_initiales()
    active_games[game.game_id] = game

    # Supprimer le duel de la liste active
    if duel_message_id in active_duels:
        del active_duels[duel_message_id]

    # Créer l'interface de jeu
    joueur_actuel = game.joueur_actuel()
    embed = GameButtonTirer(game.game_id).creer_embed_game(game, joueur_actuel)
    view = GameView(game.game_id)

    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="quitte", description="Quitter un duel (pour les joueurs qui ont rejoint)", guild=discord.Object(id=GUILD_ID))
async def quitte(interaction: discord.Interaction):
    # Chercher si l'utilisateur est dans un duel en tant que joueur (pas créateur)
    duel_to_remove = None
    duel_message_id = None

    for message_id, data in active_duels.items():
        if interaction.user in data["players"]:
            duel_to_remove = data
            duel_message_id = message_id
            break

    if not duel_to_remove:
        await interaction.response.send_message("❌ Vous n'êtes dans aucun duel!", ephemeral=True)
        return

    # Retirer le joueur du duel
    duel_to_remove["players"].remove(interaction.user)

    # Mettre à jour l'embed du duel
    try:
        channel = interaction.channel
        message = await channel.fetch_message(duel_message_id)
        embed = message.embeds[0]
        embed.clear_fields()

        embed.add_field(name="👤 Créateur", value=f"{duel_to_remove['creator'].display_name}", inline=True)
        embed.add_field(name="💰 Mise", value=f"{duel_to_remove['mise']:,} K", inline=True)
        embed.add_field(name="👥 Joueurs", value=f"{len(duel_to_remove['players']) + 1}/{duel_to_remove['max_players']}", inline=True)

        joueurs_liste = [f"• {duel_to_remove['creator'].display_name} 👑"] + [f"• {player.display_name}" for player in duel_to_remove["players"]]
        embed.add_field(
            name=f"🎮 Participants ({len(joueurs_liste)}/{duel_to_remove['max_players']})",
            value="\n".join(joueurs_liste),
            inline=False
        )

        await message.edit(embed=embed)
        await interaction.response.send_message(f"✅ Vous avez quitté le duel de {duel_to_remove['creator'].display_name}!", ephemeral=True)
    except:
        await interaction.response.send_message(f"✅ Vous avez quitté le duel!", ephemeral=True)

@bot.tree.command(name="stats", description="Voir vos statistiques de jeu avec kamas", guild=discord.Object(id=GUILD_ID))
async def stats(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    stats = get_user_stats(user_id)

    total_parties = stats["parties_gagnees"] + stats["parties_perdues"]
    taux_victoire = (stats["parties_gagnees"] / total_parties * 100) if total_parties > 0 else 0
    benefice_net = stats["kamas_gagnes"] - stats["kamas_joues"]

    embed = discord.Embed(
        title=f"📊 Statistiques de {interaction.user.display_name}",
        description="💰 **Kamas** - Scores de jeu uniquement 🎮",
        color=0x0099ff
    )

    embed.add_field(name="💰 Kamas joués", value=f"**{stats['kamas_joues']:,} K** 🎮", inline=True)
    embed.add_field(name="🎯 Kamas gagnés", value=f"**{stats['kamas_gagnes']:,} K** 🎮", inline=True)

    # Couleur différente selon le bénéfice
    benefice_color = "🟢" if benefice_net > 0 else "🔴" if benefice_net < 0 else "⚪"
    embed.add_field(name="📈 Bénéfice net", value=f"{benefice_color} **{benefice_net:,} K** 🎮", inline=True)

    embed.add_field(name="🏆 Parties gagnées", value=f"**{stats['parties_gagnees']}** ✅", inline=True)
    embed.add_field(name="💔 Parties perdues", value=f"**{stats['parties_perdues']}** ❌", inline=True)
    embed.add_field(name="📊 Taux de victoire", value=f"**{taux_victoire:.1f}%**", inline=True)

    embed.set_footer(text="🎮 Kamas - Les stats sont réinitialisées tous les lundis à 00h00")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="duels_actifs", description="Voir les duels actifs disponibles", guild=discord.Object(id=GUILD_ID))
async def duels_actifs(interaction: discord.Interaction):
    if not active_duels:
        embed = discord.Embed(
            title="🎲 Aucun duel actif",
            description="Utilisez `/duel <mise>` pour créer un nouveau duel!",
            color=0xff6666
        )
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(
        title="🎲 Duels Actifs Disponibles",
        description="Rejoignez un duel avec le bouton 'Rejoindre le duel'",
        color=0x00ff00
    )

    for i, (message_id, data) in enumerate(active_duels.items(), 1):
        places_restantes = data["max_players"] - (len(data["players"]) + 1)
        embed.add_field(
            name=f"Duel #{i} - {data['creator'].display_name}",
            value=f"💰 Mise: **{data['mise']:,} K**\n👥 Places: **{places_restantes}** restantes",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    bot.run("MTM0Mzg5MDkyNDQxMjg2NjYyMQ.GbKgKQ.H0IXvF04iqF4kAk3P0QdefjtPISSNvzzV5o_FU")




