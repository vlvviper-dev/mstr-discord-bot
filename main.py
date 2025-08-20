import discord
import requests
import asyncio
import logging
import os
from datetime import datetime
from discord.ext import tasks
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MSTRTickerBot(discord.Client):
    def __init__(self):
        # Set up intents - only need basic intents for nickname updates
        intents = discord.Intents.default()
        intents.guilds = True
        # Note: members intent not needed for updating own nickname
        super().__init__(intents=intents)
        
        # Bot configuration
        self.discord_token = os.getenv('DISCORD_BOT_TOKEN')
        self.alpha_vantage_key = os.getenv('ALPHA_VANTAGE_API_KEY', 'demo')
        self.update_interval = int(os.getenv('UPDATE_INTERVAL_MINUTES', '5'))
        
        # Price tracking
        self.current_price = None
        self.last_update = None
        self.api_call_count = 0
        
        if not self.discord_token:
            logger.error("DISCORD_BOT_TOKEN environment variable is required!")
            raise ValueError("Missing Discord bot token")
            
        logger.info(f"Bot initialized with update interval: {self.update_interval} minutes")

    async def on_ready(self):
        """Called when the bot is ready and connected to Discord"""
        if self.user:
            logger.info(f'Bot logged in as {self.user} (ID: {self.user.id})')
        logger.info(f'Connected to {len(self.guilds)} guilds')
        
        # Start the price update task
        if not self.update_price_task.is_running():
            self.update_price_task.start()
            logger.info(f'Started price update task with {self.update_interval} minute intervals')

    async def on_disconnect(self):
        """Called when the bot disconnects"""
        logger.warning('Bot disconnected from Discord')

    async def on_resumed(self):
        """Called when the bot resumes connection"""
        logger.info('Bot resumed connection to Discord')

    def fetch_mstr_price(self):
        """Fetch MSTR stock price from Alpha Vantage API"""
        try:
            # Alpha Vantage Global Quote endpoint
            url = 'https://www.alphavantage.co/query'
            params = {
                'function': 'GLOBAL_QUOTE',
                'symbol': 'MSTR',
                'apikey': self.alpha_vantage_key
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            self.api_call_count += 1
            
            # Check for API errors
            if 'Error Message' in data:
                logger.error(f"API Error: {data['Error Message']}")
                return None
                
            if 'Note' in data:
                logger.warning(f"API Limit Warning: {data['Note']}")
                return None
            
            # Extract price from response
            global_quote = data.get('Global Quote', {})
            if not global_quote:
                logger.error("No Global Quote data in API response")
                return None
                
            price_str = global_quote.get('05. price')
            if not price_str:
                logger.error("No price data in API response")
                return None
                
            price = float(price_str)
            change_str = global_quote.get('09. change', '0')
            change = float(change_str)
            
            logger.info(f"Fetched MSTR price: ${price:.2f} (Change: {change:+.2f})")
            return {
                'price': price,
                'change': change,
                'timestamp': datetime.now()
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching MSTR price: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Error parsing MSTR price data: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching MSTR price: {e}")
            return None

    def format_price_nickname(self, price_data):
        """Format the price data into a nickname string"""
        if not price_data:
            return "MSTR: Error"
            
        price = price_data['price']
        change = price_data['change']
        
        # Format change with appropriate symbol
        change_symbol = "📈" if change >= 0 else "📉"
        change_str = f"{change:+.2f}"
        
        # Create nickname (Discord has 32 character limit)
        nickname = f"$MSTR: ${price:.2f} {change_symbol}"
        
        # Ensure nickname fits Discord's limit
        if len(nickname) > 32:
            nickname = f"MSTR: ${price:.2f}"
            
        return nickname

    async def update_nickname_in_guilds(self, nickname):
        """Update bot nickname in all guilds"""
        updated_guilds = 0
        
        for guild in self.guilds:
            try:
                # Get bot member in this guild
                if self.user:
                    member = guild.get_member(self.user.id)
                else:
                    continue
                    
                if member:
                    await member.edit(nick=nickname)
                    updated_guilds += 1
                    logger.debug(f"Updated nickname in guild: {guild.name}")
                else:
                    logger.warning(f"Bot not found as member in guild: {guild.name}")
                    
            except discord.Forbidden:
                logger.warning(f"No permission to change nickname in guild: {guild.name}")
            except discord.HTTPException as e:
                logger.error(f"HTTP error updating nickname in guild {guild.name}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error updating nickname in guild {guild.name}: {e}")
                
        logger.info(f"Updated nickname in {updated_guilds}/{len(self.guilds)} guilds")
        return updated_guilds

    @tasks.loop(minutes=1)
    async def update_price_task(self):
        """Periodic task to update MSTR price and bot nickname"""
        try:
            # Check if it's time to update (respect the configured interval)
            if (self.last_update is None or 
                (datetime.now() - self.last_update).total_seconds() >= self.update_interval * 60):
                
                logger.info("Fetching updated MSTR price...")
                
                # Fetch new price data
                price_data = self.fetch_mstr_price()
                
                if price_data:
                    self.current_price = price_data
                    self.last_update = datetime.now()
                    
                    # Format and update nickname
                    nickname = self.format_price_nickname(price_data)
                    updated_count = await self.update_nickname_in_guilds(nickname)
                    
                    if updated_count > 0:
                        logger.info(f"Successfully updated price display: {nickname}")
                    else:
                        logger.warning("Failed to update nickname in any guilds")
                        
                else:
                    logger.error("Failed to fetch price data, keeping previous nickname")
                    
                    # If we have no previous price data, show error state
                    if self.current_price is None:
                        error_nickname = "MSTR: API Error"
                        await self.update_nickname_in_guilds(error_nickname)
                        
        except Exception as e:
            logger.error(f"Error in price update task: {e}")

    @update_price_task.before_loop
    async def before_update_price_task(self):
        """Wait for bot to be ready before starting the update task"""
        await self.wait_until_ready()

    async def close(self):
        """Clean shutdown of the bot"""
        logger.info("Shutting down bot...")
        if hasattr(self, 'update_price_task'):
            self.update_price_task.cancel()
        await super().close()

def main():
    """Main function to run the bot"""
    try:
        # Create and run the bot
        bot = MSTRTickerBot()
        
        # Handle graceful shutdown
        try:
            if bot.discord_token:
                bot.run(bot.discord_token)
            else:
                logger.error("No Discord bot token available")
                return 1
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        except discord.LoginFailure:
            logger.error("Invalid Discord bot token!")
        except Exception as e:
            logger.error(f"Unexpected error running bot: {e}")
            
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        return 1
        
    return 0

if __name__ == "__main__":
    exit(main())
