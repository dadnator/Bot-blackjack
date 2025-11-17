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

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Fichier de sauvegarde des données
DATA_FILE = "blackjack_data.json"

# Stockage des données
active_duels = {}     # {message_id: {"creator": user, "mise": int, "players": [], "max_players": 4}}
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

class DuelButton(discord.ui.Button):
    def __init__(self, duel_message_id):
        super().__init__(label="Rejoindre le duel", style=discord.ButtonStyle.primary, emoji="🎮")
        self.duel_message_id = duel_message_id

    async def callback(self, interaction: discord.Interaction):
        if self.duel_message_id not in active_duels:
            await interaction.response.send_message("❌ Ce duel n'existe plus!", ephemeral=True)
            return

        duel_data = active_duels[self.duel_message_id]

        if interaction.user in duel_data["players"] or interaction.user == duel_data["creator"]:
            await interaction.response.send_message("❌ Vous participez déjà à ce duel!", ephemeral=True)
            return

        if len(duel_data["players"]) + 1 >= duel_data["max_players"]:
            await interaction.response.send_message("❌ Ce duel est complet!", ephemeral=True)
            return

        duel_data["players"].append(interaction.user)

        embed = interaction.message.embeds[0]
        embed.clear_fields()

        embed.add_field(name="👤 Créateur", value=f"{duel_data['creator'].display_name}", inline=True)
        embed.add_field(name="💰 Mise", value=f"{duel_data['mise']:,} K", inline=True)
        embed.add_field(name="👥 Joueurs", value=f"{len(duel_data["players"]) + 1}/{duel_data['max_players']}", inline=True)

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

# --- Fonctions pour l'interface de Jeu (centralisées pour réutilisation) ---

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
        embed.add_field(
            name="❌ Croupier Gagne",
            value=f"Le croupier remporte le pot total de **{gain_croupier:,} K**",
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

    # --- Log du résultat ---
    log_channel = bot.get_channel(log_channel_id)
    if log_channel:
        joueurs_noms = ", ".join([p.display_name for p in game.players])
        if gagnants:
            gagnants_noms = ", ".join([g.display_name for g in gagnants])
            resultat_log = f"🎉 **VICTOIRE** : **{gagnants_noms}** remportent chacun **{gain_par_joueur:,} K** (Net)."
        elif any(game.scores[p.id] == game.croupier_score and game.scores[p.id] <= 21 for p in game.players):
             resultat_log = "🤝 **ÉGALITÉ** : Quelques joueurs ont fait Push. Mises retournées."
        else:
            resultat_log = "❌ **PERDU** : Aucun joueur n'a gagné."
        
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


    # 🚀 LOGIQUE DE RELANCE AUTOMATIQUE (MISE À JOUR) 🚀
    # La relance se fait si AUCUN joueur n'a gagné (inclut les cas 'Croupier Gagne' et 'Égalité Générale')
    if not gagnants:
        
        mise_recommencee = list(game.mises.values())[0]
        joueurs_recommences = game.players
        
        # Créer la nouvelle partie
        new_game = BlackjackGame(joueurs_recommences, mise_recommencee)
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
    # Remplacer par vos IDs de rôles si besoin
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
    
    # Pour récupérer l'ID du message que l'on vient d'envoyer, on utilise un 'defer' et 'followup.send'
    await interaction.response.defer()
    message = await interaction.followup.send(
        content=roles_ping,
        embed=embed,
        view=DuelView(interaction.id), # Utiliser l'ID de l'interaction comme clé initiale
        allowed_mentions=allowed_mentions
    )

    active_duels[interaction.id] = {
        "creator": interaction.user,
        "mise": mise,
        "players": [],
        "max_players": 4,
        "message_id": message.id
    }

@bot.tree.command(name="start", description="Lancer le duel (Créateur uniquement)", guild=discord.Object(id=GUILD_ID))
async def start(interaction: discord.Interaction):
    duel_data = None
    duel_message_id = None

    # Chercher le duel où l'utilisateur est le créateur
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
        await interaction.response.send_message("❌ Pas assez de joueurs! Attendez qu'au moins 1 joueur rejoigne (min 2 joueurs).", ephemeral=True)
        return

    all_players = [duel_data["creator"]] + duel_data["players"]

    # Créer la partie de blackjack
    game = BlackjackGame(all_players, duel_data["mise"])
    game.distribuer_cartes_initiales()
    active_games[game.game_id] = game
    
    # CORRECTION BLACKJACK NATUREL: Avancer le tour jusqu'au premier joueur qui n'est pas en stand
    joueur_actuel_apres_distrib = game.joueur_actuel()
    if joueur_actuel_apres_distrib and game.stands[joueur_actuel_apres_distrib.id]:
        # Le premier joueur a Blackjack Naturel ou a busté (bien que bust soit impossible ici), on passe au suivant
        game.joueur_suivant()
        
    # Vérifier l'état final du premier joueur (qui n'est pas en stand)
    joueur_actuel = game.joueur_actuel()

    # Supprimer le duel de la liste active
    if duel_message_id in active_duels:
        del active_duels[duel_message_id]
        
    # Suppression du message de duel précédent (optionnel)
    try:
        channel = interaction.channel
        message = await channel.fetch_message(duel_data["message_id"])
        await message.delete()
    except discord.NotFound:
        pass # Le message a déjà été supprimé ou n'existe plus

    if joueur_actuel is None:
        # Cas où TOUS les joueurs ont eu un Blackjack Naturel (la partie est finie)
        await interaction.response.defer()
        game.jouer_croupier()
        await handle_fin_de_partie(interaction, game, LOG_CHANNEL_ID)
        return

    # Créer l'interface de jeu pour le joueur qui doit commencer
    embed = creer_embed_game(game, joueur_actuel)
    view = GameView(game.game_id)

    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="quitte", description="Quitter un duel (pour les joueurs qui ont rejoint)", guild=discord.Object(id=GUILD_ID))
async def quitte(interaction: discord.Interaction):
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
        # Utiliser l'ID du message enregistré
        message = await channel.fetch_message(duel_to_remove["message_id"]) 
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
        
        # Tentative d'obtenir le lien vers le message
        try:
            message_link = f"[Aller au duel]({interaction.channel.get_partial_message(data['message_id']).jump_url})"
        except:
            message_link = "Lien non disponible"

        embed.add_field(
            name=f"Duel #{i} - {data['creator'].display_name}",
            value=(
                f"💰 Mise: **{data['mise']:,} K**\n"
                f"👥 Places: **{places_restantes}** restantes\n"
                f"{message_link}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed)

charger_donnees()
keep_alive()
bot.run(token)
