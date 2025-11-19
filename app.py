import discord
from discord.ext import commands, tasks
from discord import app_commands
from keep_alive import keep_alive
import asyncio
import random
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# --- CONFIGURATION & CONSTANTES ---
# Assurez-vous que 'TOKEN_BOT_DISCORD' est défini dans vos variables d'environnement
# NOTE: Le token doit être défini dans l'environnement du serveur de déploiement (comme Render ou Replit)
token = os.environ['TOKEN_BOT_DISCORD']

# Remplacer les IDs par vos IDs réels
GUILD_ID = 1366369136648654868
CHANNEL_ID = 1394960912435122257
LOG_CHANNEL_ID = 1366384335615164529 
# ID DU RÔLE CROUPIER (Assurez-vous que cet ID est correct)
ROLE_CROUPIER_ID = 1401471414262829066 
ROLE_AUTRE_ID = 1366378672281620495 # Utilisé seulement pour le ping initial

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Fichier de sauvegarde des données
DATA_FILE = "blackjack_data.json"

# Stockage des données
# Clé = message_id pour une meilleure fiabilité
# CHANGEMENT: Ajout de "croupier_assigne"
active_duels = {}     # {message_id: {"creator": user, "mise": int, "players": [], "max_players": 4, "message_id": int, "croupier_assigne": Optional[discord.Member]}}
active_games = {}     # {game_id: BlackjackGame object}
player_stats = {}     # {user_id: {"kamas_joues": int, "kamas_gagnes": int, "parties_gagnees": int, "parties_perdues": int}}

def charger_donnees():
    global player_stats
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                data = json.load(f)
                player_stats = data.get("player_stats", {})
            except json.JSONDecodeError:
                player_stats = {} # Fichier corrompu, on réinitialise

def sauvegarder_donnees():
    with open(DATA_FILE, 'w') as f:
        json.dump({"player_stats": player_stats}, f, indent=4)

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
        self.calculer_score_croupier()
        self.croupier_blackjack = (len(self.croupier_hand) == 2 and self.croupier_score == 21)

    def tirer_carte(self):
        # Retourne une valeur de carte correcte : 1 (As), 2-9, 10 pour 10/J/Q/K
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
        if 0 <= self.current_player_index < len(self.players):
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

    def jouer_croupier(self):
        # Le croupier tire jusqu'à avoir au moins 17 (prise en compte des As)
        while self.calculer_score_croupier() < 17:
            self.croupier_hand.append(self.tirer_carte())
            self.calculer_score_croupier() # Recalculer après chaque tirage

    def determiner_gagnants(self):
        gagnants = []
        self.calculer_score_croupier()

        for player in self.players:
            player_score = self.scores[player.id]

            # 1. Le joueur perd automatiquement s'il dépasse 21
            if player_score > 21:
                continue

            player_natural = self.natural_blackjack.get(player.id, False)
            dealer_natural = self.croupier_blackjack

            # 2. Le croupier a busté
            if self.croupier_score > 21:
                gagnants.append(player)
                continue

            # 3. Comparaison des Blackjacks Naturels
            if player_natural and not dealer_natural:
                # BJ naturel bat tout sauf BJ naturel du croupier
                gagnants.append(player)
                continue
            if dealer_natural and not player_natural:
                # Le joueur perd
                continue
            
            # Si les deux ont un BJ naturel, c'est un 'push' (égalité)
            if dealer_natural and player_natural:
                continue

            # 4. Comparaison des scores standards (<= 21)
            if player_score > self.croupier_score:
                gagnants.append(player)
            # 5. Égalité (Push)
            if player_score == self.croupier_score:
                continue

        return gagnants

# --- FONCTIONS UTILITAIRES POUR L'EMBED DU DUEL ---

def creer_embed_duel(duel_data: Dict):
    embed = discord.Embed(
        title="🎲 Duel de Blackjack Multi-Joueurs",
        description=f"**{duel_data['creator'].display_name}** a lancé un duel de blackjack ! Le **Croupier** doit s'assigner pour lancer la partie.",
        color=0x00ff00
    )
    
    croupier_name = duel_data["croupier_assigne"].display_name if duel_data["croupier_assigne"] else "❌ Non assigné"

    embed.add_field(name="👤 Créateur", value=f"{duel_data['creator'].display_name}", inline=True)
    embed.add_field(name="💰 Mise", value=f"{duel_data['mise']:,} K", inline=True)
    embed.add_field(name="👥 Joueurs", value=f"{len(duel_data['players']) + 1}/{duel_data['max_players']}", inline=True)
    embed.add_field(name="🤵 Croupier Assigné", value=croupier_name, inline=False) # Nouveau champ
    
    joueurs_liste = [f"• {duel_data['creator'].display_name} 👑"] + [f"• {player.display_name}" for player in duel_data["players"]]
    embed.add_field(
        name=f"🎮 Participants ({len(joueurs_liste)}/{duel_data['max_players']})",
        value="\n".join(joueurs_liste),
        inline=False
    )
    embed.set_footer(text="Cliquez sur 'Rejoindre le duel' pour participer. Maximum 4 joueurs.")
    
    return embed


# --- NOUVEAUX BOUTONS DE GESTION DU DUEL ---

class CroupierAssignButton(discord.ui.Button):
    def __init__(self, duel_message_id):
        super().__init__(label="S'assigner (Croupier)", style=discord.ButtonStyle.secondary, emoji="🤝")
        self.duel_message_id = duel_message_id

    async def callback(self, interaction: discord.Interaction):
        # 1. Vérification stricte du rôle Croupier
        is_croupier = interaction.user.get_role(ROLE_CROUPIER_ID) is not None
        
        if not is_croupier:
            await interaction.response.send_message("❌ Seul un utilisateur avec le rôle **Croupier** peut s'assigner.", ephemeral=True)
            return

        # 2. Chercher le duel via l'ID du message
        duel_key = self.duel_message_id
        duel_data = active_duels.get(duel_key)
        
        if not duel_data:
            await interaction.response.send_message("❌ Ce duel n'existe plus.", ephemeral=True)
            return
            
        # 3. Assignation
        # On permet à un autre croupier de prendre la place
        duel_data["croupier_assigne"] = interaction.user
        
        # 4. Mise à jour de l'interface
        embed = creer_embed_duel(duel_data)
        view = DuelView(self.duel_message_id)

        await interaction.response.edit_message(embed=embed, view=view)
        # Message éphémère pour confirmer l'action
        await interaction.followup.send(f"✅ Vous êtes maintenant assigné(e) au duel !", ephemeral=True)

class CroupierStartButton(discord.ui.Button):
    def __init__(self, duel_message_id):
        # Étiquette plus explicite pour le Croupier
        super().__init__(label="Croupier : Lancer la partie", style=discord.ButtonStyle.danger, emoji="🚀")
        self.duel_message_id = duel_message_id

    async def callback(self, interaction: discord.Interaction):
        # 1. Vérification stricte du rôle Croupier
        is_croupier = interaction.user.get_role(ROLE_CROUPIER_ID) is not None
        
        if not is_croupier:
            await interaction.response.send_message("❌ Seul le **Croupier** peut lancer un duel.", ephemeral=True)
            return

        # 2. Chercher le duel via l'ID du message (Clé stable)
        duel_key = self.duel_message_id 
        duel_data = active_duels.get(duel_key)
        
        if not duel_data:
            await interaction.response.send_message("❌ Ce duel n'existe plus ou est déjà lancé.", ephemeral=True)
            return
            
        # 2.1. Vérification que le croupier est assigné (BONUS: permet à n'importe quel croupier de lancer)
        if duel_data["croupier_assigne"] is None:
            await interaction.response.send_message("⚠️ Le Croupier doit d'abord s'assigner au duel avec le bouton 🤝 pour confirmer la prise en charge.", ephemeral=True)
            return

        # 3. Vérification du nombre de joueurs
        total_players = len(duel_data["players"]) + 1 # Créateur + joueurs
        if total_players < 2:
            await interaction.response.send_message("❌ Pas assez de joueurs! Attendez qu'au moins 1 joueur rejoigne (min 2 joueurs).", ephemeral=True)
            return

        all_players = [duel_data["creator"]] + duel_data["players"]

        # 4. Créer la partie de blackjack
        game = BlackjackGame(all_players, duel_data["mise"])
        game.distribuer_cartes_initiales()
        active_games[game.game_id] = game
        
        # Avancer le tour pour gérer le Blackjack Naturel initial
        joueur_actuel_apres_distrib = game.joueur_actuel()
        if joueur_actuel_apres_distrib and game.stands[joueur_actuel_apres_distrib.id]:
            game.joueur_suivant()
            
        joueur_actuel = game.joueur_actuel()

        # Supprimer le duel de la liste active
        if duel_key in active_duels:
            del active_duels[duel_key]

        # 5. Lancer l'interface de jeu

        if joueur_actuel is None:
            # Cas où TOUS les joueurs ont eu un Blackjack Naturel
            await interaction.response.defer() 
            game.jouer_croupier()
            # Mettre à jour le message de duel en "Partie Lancée" (ou le supprimer)
            await interaction.message.edit(content="Partie lancée ! Le résultat suit...", embed=None, view=None)
            await handle_fin_de_partie(interaction, game, LOG_CHANNEL_ID)
            return

        # Créer l'interface de jeu pour le joueur qui doit commencer
        embed = creer_embed_game(game, joueur_actuel)
        view = GameView(game.game_id)
        
        # 6. Éditer le message de duel avec la nouvelle interface de jeu
        await interaction.response.edit_message(content=f"Partie lancée par {interaction.user.display_name} (Croupier)!", embed=embed, view=view)


class DuelButton(discord.ui.Button):
    def __init__(self, duel_message_id):
        super().__init__(label="Rejoindre le duel", style=discord.ButtonStyle.primary, emoji="🎮")
        self.duel_message_id = duel_message_id

    async def callback(self, interaction: discord.Interaction):
        # Chercher le duel via l'ID du message (Clé stable)
        duel_key = self.duel_message_id
        duel_data = active_duels.get(duel_key)
                
        if not duel_data:
            await interaction.response.send_message("❌ Ce duel n'existe plus!", ephemeral=True)
            return

        if interaction.user in duel_data["players"] or interaction.user == duel_data["creator"]:
            await interaction.response.send_message("❌ Vous participez déjà à ce duel!", ephemeral=True)
            return

        if len(duel_data["players"]) + 1 >= duel_data["max_players"]:
            await interaction.response.send_message("❌ Ce duel est complet!", ephemeral=True)
            return

        duel_data["players"].append(interaction.user)
        # Pas besoin de réassigner, l'objet est modifié en place
        

        embed = creer_embed_duel(duel_data)
        
        view_to_send = DuelView(self.duel_message_id) # La vue inclut les deux boutons

        await interaction.message.edit(embed=embed, view=view_to_send)
        await interaction.response.send_message(f"✅ Vous avez rejoint le duel de {duel_data['creator'].display_name}!", ephemeral=True)

class DuelView(discord.ui.View):
    def __init__(self, duel_message_id):
        super().__init__(timeout=None)
        # 1. Bouton pour rejoindre (Joueurs)
        self.add_item(DuelButton(duel_message_id))
        # 2. Bouton pour s'assigner (Croupier)
        self.add_item(CroupierAssignButton(duel_message_id))
        # 3. Bouton pour lancer (Croupier)
        self.add_item(CroupierStartButton(duel_message_id))

# --- Fonctions pour l'interface de Jeu ---

def creer_embed_game(game: BlackjackGame, joueur_suivant: Optional[discord.Member]):
    embed = discord.Embed(title="🎲 TABLE DE BLACKJACK", color=0xffff00)

    # Bloc croupier : une carte visible et l'autre cachée
    croupier_hand_display = [str(game.croupier_hand[0])] + ['❓']*(len(game.croupier_hand)-1)
    
    embed.add_field(
        name="🎯 Croupier",
        value=f"{croupier_hand_display} (?)",
        inline=False
    )
    # Ligne vide pour espacement
    embed.add_field(name="-----", value="\u200b", inline=False)  

    # Bloc joueurs
    for player in game.players:
        statut = ""
        score = game.scores[player.id]
        
        if game.natural_blackjack[player.id]:
            statut = "✨ Blackjack Naturel!"
        elif score > 21:
            statut = "💥 Dépassé (Bust!)"
        elif player == joueur_suivant:
            statut = "⏳ C'est à vous de jouer!"
        elif game.stands[player.id]:
            statut = "✋ Reste"
            
        embed.add_field(
            name=f"👤 {player.display_name}",
            value=f"{game.hands[player.id]} ({score}) {statut}",
            inline=False
        )
        embed.add_field(name="-----", value="\u200b", inline=False) 

    return embed

def creer_embed_fin(game: BlackjackGame, gagnants: List[discord.Member], gain_par_joueur: int, gain_croupier: int):
    embed = discord.Embed(title="🎲 TABLE DE BLACKJACK - FIN DE PARTIE", color=0x00ff00 if gagnants else 0xff0000)

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
            statut = f"🎉 Gagnant! (+{gain_par_joueur:,} K)"
        elif game.scores[player.id] > 21:
            statut = "💥 Dépassé!"
        elif game.scores[player.id] == game.croupier_score and game.scores[player.id] <= 21:
            statut = "🤝 Égalité (Push)"
        elif game.croupier_blackjack and game.natural_blackjack[player.id]:
             statut = "🤝 Égalité (Double BJ)" # Cas BJ vs BJ croupier
        else:
            statut = "❌ Perdu"

        embed.add_field(
            name=f"👤 {player.display_name}",
            value=f"{game.hands[player.id]} ({game.scores[player.id]}) - {statut}",
            inline=False
        )

    embed.add_field(name="-----", value="\u200b", inline=False) 
    
    # Résultat financier
    embed.add_field(
        name="💰 Total des Mises en jeu",
        value=f"**{game.pot_total:,} K**",
        inline=True
    )

    if gagnants:
        noms = ", ".join([g.display_name for g in gagnants])
        embed.add_field(
            name="🏆 Gains Distribués",
            value=f"{noms} reçoivent chacun **{gain_par_joueur:,} K**.",
            inline=True
        )
        embed.add_field(
            name="🏦 Croupier Récupère",
            value=f"**{gain_croupier:,} K** (Commission)",
            inline=True
        )
    else:
        # On ne précise pas si c'est un push ou une perte simple dans ce bloc
        embed.add_field(
            name="❌ Croupier Gagne / Égalité",
            value=f"Le pot reste à la table ou les mises sont retournées. Croupier récupère **{gain_croupier:,} K** (Commission incluse)",
            inline=True
        )

    return embed

async def handle_fin_de_partie(interaction: discord.Interaction, game: BlackjackGame, log_channel_id: int):
    gagnants = game.determiner_gagnants()
    
    # 5% de commission
    commission = int(game.pot_total * 0.05)
    pot_a_distribuer = game.pot_total - commission
    
    if gagnants:
        # Gain par joueur gagnant
        gain_par_joueur = int(pot_a_distribuer / len(gagnants))
        # Reste de la commission + ce qui n'a pu être distribué
        gain_croupier = commission + (pot_a_distribuer - (gain_par_joueur * len(gagnants)))
    else:
        # Le croupier gagne le pot total (ou c'est un push général)
        gain_par_joueur = 0
        gain_croupier = game.pot_total

    # Mise à jour des statistiques
    for player in game.players:
        stats = get_user_stats(player.id)
        stats["kamas_joues"] += game.mises[player.id]
        if player in gagnants:
            stats["kamas_gagnes"] += gain_par_joueur + game.mises[player.id] # Mise retournée + gain net
            stats["parties_gagnees"] += 1
        else:
            # Si 'push', le kamas_gagnes est égal au kamas_joues (mise retournée)
            if game.scores[player.id] == game.croupier_score and game.scores[player.id] <= 21:
                stats["kamas_gagnes"] += game.mises[player.id] # Mise retournée
            else:
                stats["parties_perdues"] += 1

    # --- Log du résultat (Seulement si des joueurs ont gagné) ---
    log_channel = bot.get_channel(log_channel_id)
    if log_channel and gagnants:
        
        joueurs_noms = ", ".join([p.display_name for p in game.players])
        gagnants_noms = ", ".join([g.display_name for g in gagnants])
        
        resultat_log = f"🎉 **VICTOIRE** : **{gagnants_noms}** remportent chacun **{gain_par_joueur:,} K** (Net)."
        
        message_log = (
            f"--- **Résultat Duel Blackjack** ---\n"
            f"**ID Partie** : {game.game_id}\n"
            f"**Croupier** : {game.croupier_hand} ({game.croupier_score})\n"
            f"**Participants** ({len(game.players)}) : {joueurs_noms}\n"
            f"**Mise par joueur** : {list(game.mises.values())[0]:,} K\n"
            f"{resultat_log}\n"
            f"**Commission (5%)** : {commission:,} K"
        )
        await log_channel.send(message_log)

    # --- Mise à jour de l'interface de jeu ---
    embed_fin = creer_embed_fin(game, gagnants, gain_par_joueur, gain_croupier)
    
    # Stocker l'information si l'interaction a déjà été répondue
    is_response_done = interaction.response.is_done()
    
    # Nettoyage de l'ancienne partie
    if game.game_id in active_games:
        del active_games[game.game_id]
    sauvegarder_donnees()


    # 🚀 LOGIQUE DE RELANCE AUTOMATIQUE 🚀
    if not gagnants:
        
        mise_recommencee = list(game.mises.values())[0]
        joueurs_recommencees = game.players
        
        # Créer la nouvelle partie
        new_game = BlackjackGame(joueurs_recommencees, mise_recommencee)
        new_game.distribuer_cartes_initiales()
        active_games[new_game.game_id] = new_game
        
        # Avancer l'index pour gérer le Blackjack Naturel dans la nouvelle partie
        new_joueur_actuel = new_game.joueur_actuel()
        if new_joueur_actuel and new_game.stands[new_joueur_actuel.id]:
            new_game.joueur_suivant()
        new_joueur_actuel = new_game.joueur_actuel()
        
        # Créer la nouvelle interface de jeu
        embed_nouvelle_partie = creer_embed_game(new_game, new_joueur_actuel)
        view_nouvelle_partie = GameView(new_game.game_id)
        
        # 1. Afficher le résultat de la partie FINIE
        if is_response_done:
            await interaction.message.edit(embed=embed_fin, view=None)
            
            # Message ajusté pour couvrir le cas 'Push' aussi
            message_content = "🔄 **RELANCE AUTOMATIQUE** : La partie est finie (Croupier gagnant ou Égalité). Nouvelle partie lancée immédiatement!"
            
            # 2. Afficher la nouvelle partie juste après dans un nouveau message
            await interaction.channel.send(
                content=message_content,
                embed=embed_nouvelle_partie,
                view=view_nouvelle_partie
            )
        else:
            await interaction.response.edit_message(embed=embed_fin, view=None)
            
            message_content = "🔄 **RELANCE AUTOMATIQUE** : La partie est finie (Croupier gagnant ou Égalité). Nouvelle partie lancée immédiatement!"
            
            await interaction.channel.send(
                content=message_content,
                embed=embed_nouvelle_partie,
                view=view_nouvelle_partie
            )
            
    else:
        # Si des joueurs ont gagné (gagnants non vide), le jeu s'arrête
        if is_response_done:
            await interaction.message.edit(embed=embed_fin, view=None)
        else:
            await interaction.response.edit_message(embed=embed_fin, view=None)
    
class GameButtonTirer(discord.ui.Button):
    def __init__(self, game_id):
        super().__init__(label="Tirer une carte", style=discord.ButtonStyle.primary, emoji="🃏")
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

        nouveau_score = game.tirer_carte_joueur(interaction.user.id)
        
        if nouveau_score >= 21:
            # Le joueur a busté ou a atteint 21, il se met en stand
            game.stands[interaction.user.id] = True
            game.joueur_suivant() # Passe au joueur suivant
        
        joueur_suivant = game.joueur_actuel() 

        await self.mettre_a_jour_interface(interaction, game, joueur_suivant)

    async def mettre_a_jour_interface(self, interaction, game, joueur_suivant):
        if joueur_suivant:
            embed = creer_embed_game(game, joueur_suivant)
            view = GameView(self.game_id)
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            # Tous les joueurs ont fini, le croupier joue
            game.jouer_croupier()
            await handle_fin_de_partie(interaction, game, LOG_CHANNEL_ID) 

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

        if joueur_suivant:
            embed = creer_embed_game(game, joueur_suivant)
            view = GameView(self.game_id)
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            # Tous les joueurs ont fini, le croupier joue
            game.jouer_croupier()
            await handle_fin_de_partie(interaction, game, LOG_CHANNEL_ID)

class GameView(discord.ui.View):
    def __init__(self, game_id):
        # Timeout augmenté pour donner le temps aux joueurs de réagir
        super().__init__(timeout=300) 
        self.add_item(GameButtonTirer(game_id))
        self.add_item(GameButtonRester(game_id))


# --- Tâches et initialisation ---

@tasks.loop(hours=24)
async def reset_stats_hebdo():
    # Déterminer si c'est lundi 00:00 (ou la première exécution après)
    now = datetime.now()
    if now.weekday() == 0 and now.hour == 0:
        # Réinitialisation des statistiques ici (à implémenter)
        print(f"[{now}] Réinitialisation hebdomadaire des statistiques.")
        # Exemple : réinitialiser certaines stats si vous le souhaitez
        # for user_id in player_stats:
        #     player_stats[user_id]["kamas_joues"] = 0
        #     player_stats[user_id]["kamas_gagnes"] = 0
        sauvegarder_donnees()
    else:
        print(f"[{now}] Tâche reset_stats_hebdo exécutée, mais pas le bon moment (Lundi 00:00).")

@reset_stats_hebdo.before_loop
async def before_reset_stats_hebdo():
    await bot.wait_until_ready()
    # Logique pour attendre Lundi 00:00 la première fois (non implémentée ici pour simplicité)
    print("La tâche reset_stats_hebdo est prête.")

# --- ÉVÉNEMENTS DU BOT ---

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)

    # Sync only your guild commands
    try:
        await bot.tree.sync(guild=guild)
        print(f"Commandes synchronisées pour la guilde ID: {GUILD_ID}")
    except Exception as e:
        print(f"Échec de la synchronisation des commandes pour la guilde : {e}")
        
    print(f'{bot.user} est connecté!')
    
    # DÉMARRER LA TÂCHE ICI (SOLUTION AU RuntimeError)
    if not reset_stats_hebdo.is_running():
        reset_stats_hebdo.start()

# --- COMMANDES SLASH ---

@bot.tree.command(name="duel", description="Créer un duel de blackjack avec une mise", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(mise="La mise en kamas que vous voulez jouer")
async def duel(interaction: discord.Interaction, mise: int):
    if mise <= 0:
        await interaction.response.send_message("❌ La mise doit être supérieure à 0!", ephemeral=True)
        return

    # ID des rôles à ping
    roles_ping = f"<@&{ROLE_CROUPIER_ID}> <@&{ROLE_AUTRE_ID}>"
    
    # Préparer les données initiales du duel
    initial_duel_data = {
        "creator": interaction.user,
        "mise": mise,
        "players": [],
        "max_players": 4,
        "message_id": None, # Sera mis à jour après l'envoi
        "croupier_assigne": None # Nouveau champ
    }
    
    embed = creer_embed_duel(initial_duel_data)

    # Envoi avec ping autorisé pour les rôles
    allowed_mentions = discord.AllowedMentions(roles=True)
    
    # Pour récupérer l'ID du message que l'on vient d'envoyer, on utilise un 'defer' et 'followup.send'
    await interaction.response.defer()
    message = await interaction.followup.send(
        content=roles_ping,
        embed=embed,
        view=DuelView(interaction.id), # Utilise l'ID de l'interaction pour l'initialisation temporaire de la vue
        allowed_mentions=allowed_mentions
    )
    
    # CLÉ DU DUEL = ID DU MESSAGE (plus stable)
    duel_key = message.id
    
    # Mettre à jour l'objet avec l'ID du message réel
    initial_duel_data["message_id"] = duel_key
    
    # Mettre à jour la vue avec l'ID du message réel
    await message.edit(view=DuelView(duel_key)) 
    
    # Enregistre le duel avec le message.id comme clé
    active_duels[duel_key] = initial_duel_data


@bot.tree.command(name="quitte", description="Quitter un duel (pour les joueurs qui ont rejoint)", guild=discord.Object(id=GUILD_ID))
async def quitte(interaction: discord.Interaction):
    duel_to_remove = None
    duel_key_to_remove = None

    # Cherche si l'utilisateur est dans un duel
    for key, data in active_duels.items():
        if interaction.user in data["players"]:
            duel_to_remove = data
            duel_key_to_remove = key
            break

    if not duel_to_remove:
        await interaction.response.send_message("❌ Vous n'êtes dans aucun duel!", ephemeral=True)
        return

    # Retirer le joueur du duel
    duel_to_remove["players"].remove(interaction.user)
    
    # Mise à jour de l'objet dans le dictionnaire
    active_duels[duel_key_to_remove] = duel_to_remove

    # Mettre à jour l'embed du duel
    try:
        channel = interaction.channel
        # Utiliser l'ID du message enregistré
        message = await channel.fetch_message(duel_to_remove["message_id"]) 
        
        # Recréer l'embed et la vue
        embed = creer_embed_duel(duel_to_remove)
        view_to_send = DuelView(duel_key_to_remove) 

        await message.edit(embed=embed, view=view_to_send)
        await interaction.response.send_message(f"✅ Vous avez quitté le duel de {duel_to_remove['creator'].display_name}!", ephemeral=True)
    except Exception as e:
        # En cas d'erreur de récupération du message (ex: message supprimé)
        print(f"Erreur lors de la mise à jour du message de duel: {e}")
        await interaction.response.send_message(f"✅ Vous avez quitté un duel actif (le message a pu être supprimé).", ephemeral=True)
        if duel_key_to_remove in active_duels:
             del active_duels[duel_key_to_remove] # On nettoie la liste

@bot.tree.command(name="stats", description="Voir vos statistiques de jeu avec kamas", guild=discord.Object(id=GUILD_ID))
async def stats(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    stats = get_user_stats(user_id)

    total_parties = stats["parties_gagnees"] + stats["parties_perdues"]
    taux_victoire = (stats["parties_gagnees"] / total_parties * 100) if total_parties > 0 else 0
    
    # Le bénéfice net est l'argent gagné (mises retournées incluses) moins l'argent parié.
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

    embed.set_footer(text="🎮 Kamas - Les statistiques sont conservées à moins d'une réinitialisation manuelle ou automatique.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

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
        description="Rejoignez un duel dans le salon où il a été créé en cliquant sur le bouton.",
        color=0x00ff00
    )

    for i, (message_id, data) in enumerate(active_duels.items(), 1):
        places_restantes = data["max_players"] - (len(data["players"]) + 1)
        croupier_name = data["croupier_assigne"].display_name if data["croupier_assigne"] else "Non assigné" # Ajout du croupier assigné
        
        # Tentative d'obtenir le lien vers le message
        try:
            # message_id est maintenant la clé (message.id)
            message_link = f"[Aller au duel]({interaction.channel.get_partial_message(message_id).jump_url})"
        except:
            message_link = "Lien non disponible"

        embed.add_field(
            name=f"Duel #{i} - {data['creator'].display_name}",
            value=(
                f"💰 Mise: **{data['mise']:,} K**\n"
                f"🤵 Croupier: **{croupier_name}**\n"
                f"👥 Places: **{places_restantes}** restantes\n"
                f"{message_link}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed)

charger_donnees()
keep_alive()
bot.run(token)
