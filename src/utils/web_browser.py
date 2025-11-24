"""Web browsing utilities for Researcher agent"""

import requests
from typing import Optional, Dict, Any, List
from datetime import date, datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import re
from src.utils.logging import get_logger
from src.utils.config import config

# Try to import ddgs library (renamed from duckduckgo_search), fallback to HTML scraping if not available
try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    # Try old package name for backwards compatibility
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

logger = get_logger("utils.web_browser")


class WebBrowser:
    """Web browser utility for scraping and searching"""
    
    def __init__(self):
        """Initialize web browser"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.logger = logger
        
        # Initialize KenPom scraper if credentials are available
        self.kenpom_scraper = None
        if config.is_kenpom_enabled():
            try:
                from src.data.scrapers.kenpom_scraper import KenPomScraper
                self.kenpom_scraper = KenPomScraper()
                if self.kenpom_scraper.is_authenticated():
                    self.logger.info("✓ KenPom scraper initialized and authenticated")
                else:
                    self.logger.warning("KenPom scraper initialized but authentication failed")
                    self.kenpom_scraper = None
            except Exception as e:
                self.logger.warning(f"Failed to initialize KenPom scraper: {e}")
                self.kenpom_scraper = None
    
    def search_web(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search the web using DuckDuckGo (prefers API, falls back to HTML scraping)
        
        Args:
            query: Search query
            max_results: Maximum number of results to return
            
        Returns:
            List of search results with title, url, snippet
        """
        # Detect if this is a basketball-related query
        query_lower = query.lower()
        is_basketball_query = any(term in query_lower for term in ['basketball', 'ncaab', 'hoops', 'cbb', 'college basketball'])
        # Try DuckDuckGo Search API first (more reliable)
        if DDGS_AVAILABLE:
            try:
                with DDGS() as ddgs:
                    results = []
                    # Use text search - the API returns a generator
                    try:
                        search_results = ddgs.text(query, max_results=max_results)
                        # Convert generator to list
                        search_list = list(search_results)
                        
                        for r in search_list:
                            # Handle different response formats (dict keys may vary)
                            title = r.get('title') or r.get('Title', '') or ''
                            url = r.get('href') or r.get('Href', '') or r.get('url', '') or ''
                            snippet = r.get('body') or r.get('Body', '') or r.get('snippet', '') or ''
                            
                            if title and url:
                                # Filter out football results if this is a basketball query
                                if is_basketball_query:
                                    url_lower = url.lower()
                                    title_lower = title.lower()
                                    snippet_lower = snippet.lower()
                                    football_keywords = ['football', 'nfl', 'ncaaf', 'college football', 'cfb']
                                    if any(keyword in url_lower or keyword in title_lower or keyword in snippet_lower for keyword in football_keywords):
                                        self.logger.debug(f"Filtered out football result: {url}")
                                        continue
                                
                                results.append({
                                    'title': title,
                                    'url': url,
                                    'snippet': snippet
                                })
                        
                        if results:
                            self.logger.info(f"Found {len(results)} search results for: {query} (using DDGS API)")
                            return results
                        else:
                            self.logger.warning(f"DDGS API returned 0 results for: {query}")
                            # Try HTML fallback if API returns 0 results
                            self.logger.info("Attempting HTML scraping fallback...")
                    except StopIteration:
                        # Generator exhausted with no results
                        self.logger.warning(f"DDGS API generator exhausted with no results for: {query}")
                        # Try HTML fallback
                    except Exception as api_error:
                        self.logger.warning(f"DDGS API iteration error: {api_error}, trying HTML fallback")
                        # Continue to HTML fallback
            except Exception as e:
                self.logger.warning(f"DDGS API initialization error: {e}, falling back to HTML scraping")
                # Continue to HTML fallback
        
        # Fallback to HTML scraping (if DDGS API not available, failed, or returned 0 results)
        # Note: DuckDuckGo HTML search often returns 403 Forbidden for automated requests
        # We'll try it anyway as a last resort
        try:
            # Use DuckDuckGo HTML search (no API key needed)
            search_url = "https://html.duckduckgo.com/html/"
            params = {
                'q': query,
                'kl': 'us-en'
            }
            
            # Enhanced headers to avoid bot detection
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Referer': 'https://duckduckgo.com/'
            }
            
            response = self.session.get(search_url, params=params, headers=headers, timeout=15)
            
            # Check for 403 Forbidden (common when blocked)
            if response.status_code == 403:
                self.logger.warning(f"DuckDuckGo HTML search blocked (403 Forbidden) for query: {query}")
                self.logger.warning("This is common with automated requests. Install 'ddgs' package for better results.")
                return []
            
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            # Try multiple parsing strategies for DuckDuckGo results
            # Strategy 1: Look for result divs with class 'result'
            result_divs = soup.find_all('div', class_='result')
            
            # Strategy 2: If no results, try looking for links in result containers
            if not result_divs:
                # Try alternative class names
                result_divs = soup.find_all('div', class_='web-result')
                if not result_divs:
                    result_divs = soup.find_all('div', {'class': lambda x: x and 'result' in x.lower()})
            
            # Strategy 3: Look for result links directly
            if not result_divs:
                # Try finding result links directly
                result_links = soup.find_all('a', class_='result__a')
                if result_links:
                    for link in result_links[:max_results]:
                        title = link.get_text(strip=True)
                        url = link.get('href', '')
                        
                        if url:
                            # Fix DuckDuckGo URLs
                            if url.startswith('//'):
                                url = 'https:' + url
                            elif url.startswith('/'):
                                continue
                            elif not url.startswith('http'):
                                continue
                            
                            # Try to find snippet nearby
                            snippet = ""
                            parent = link.find_parent()
                            if parent:
                                snippet_elem = parent.find('a', class_='result__snippet')
                                if snippet_elem:
                                    snippet = snippet_elem.get_text(strip=True)
                            
                            results.append({
                                'title': title,
                                'url': url,
                                'snippet': snippet
                            })
            
            # Parse results from divs
            for div in result_divs[:max_results]:
                # Try multiple ways to find title and URL
                title_elem = div.find('a', class_='result__a')
                if not title_elem:
                    title_elem = div.find('a', {'class': lambda x: x and 'result' in str(x).lower()})
                if not title_elem:
                    title_elem = div.find('a')
                
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get('href', '')
                    
                    # Fix DuckDuckGo URLs that start with // (missing scheme)
                    if url.startswith('//'):
                        url = 'https:' + url
                    elif url.startswith('/'):
                        # Relative URL, skip it
                        continue
                    elif not url.startswith('http'):
                        # Invalid URL, skip it
                        continue
                    
                    # Try to find snippet
                    snippet = ""
                    snippet_elem = div.find('a', class_='result__snippet')
                    if not snippet_elem:
                        snippet_elem = div.find('div', class_='result__snippet')
                    if snippet_elem:
                        snippet = snippet_elem.get_text(strip=True)
                    
                    if title and url:
                        results.append({
                            'title': title,
                            'url': url,
                            'snippet': snippet
                        })
            
            # Remove duplicates based on URL and filter football results for basketball queries
            seen_urls = set()
            unique_results = []
            for result in results:
                url = result.get('url', '')
                if not url or url in seen_urls:
                    continue
                
                # Filter out football results if this is a basketball query
                if is_basketball_query:
                    url_lower = url.lower()
                    title_lower = result.get('title', '').lower()
                    snippet_lower = result.get('snippet', '').lower()
                    football_keywords = ['football', 'nfl', 'ncaaf', 'college football', 'cfb']
                    if any(keyword in url_lower or keyword in title_lower or keyword in snippet_lower for keyword in football_keywords):
                        self.logger.debug(f"Filtered out football result: {url}")
                        continue
                
                seen_urls.add(url)
                unique_results.append(result)
            
            self.logger.info(f"Found {len(unique_results)} search results for: {query} (using HTML scraping)")
            
            # Debug: log if no results found
            if len(unique_results) == 0:
                self.logger.warning(f"⚠️  No search results found for query: {query}")
                self.logger.debug(f"Response status: {response.status_code}, Content length: {len(response.text)}")
                # Log a sample of the HTML to help debug
                if len(response.text) > 0:
                    self.logger.debug(f"HTML sample (first 500 chars): {response.text[:500]}")
            
            return unique_results[:max_results]
                
        except Exception as e:
            self.logger.error(f"Error in HTML scraping fallback for '{query}': {e}", exc_info=True)
            # If both methods fail, return empty list
            # The Researcher will need to work with available data
            self.logger.error(f"⚠️  All web search methods failed for: {query}")
            return []
    
    def fetch_url(self, url: str, max_length: int = 5000) -> Optional[str]:
        """
        Fetch and extract text content from a URL, focusing on main content
        
        Args:
            url: URL to fetch
            max_length: Maximum length of extracted text
            
        Returns:
            Extracted text content or None if error
        """
        try:
            # Fix URLs that start with // (missing scheme)
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('/'):
                # Relative URL, can't fetch
                self.logger.warning(f"Relative URL provided: {url}")
                return None
            elif not url.startswith('http'):
                # Invalid URL
                self.logger.warning(f"Invalid URL format: {url}")
                return None
            
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove unwanted elements first
            for element in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
                element.decompose()
            
            # Try to find main content area (common patterns)
            main_content = None
            content_selectors = [
                'article',
                'main',
                '[role="main"]',
                '.article-content',
                '.post-content',
                '.entry-content',
                '.content',
                '.main-content',
                '#content',
                '#main-content',
                '.story-body',
                '.article-body'
            ]
            
            for selector in content_selectors:
                main_content = soup.select_one(selector)
                if main_content:
                    break
            
            # If no main content found, try to remove common noise and use body
            if not main_content:
                # Remove common navigation/ad elements
                for element in soup.find_all(['nav', 'header', 'footer', 'aside', 'form', 'button']):
                    element.decompose()
                main_content = soup.find('body') or soup
            
            # Get text from main content
            if main_content:
                text = main_content.get_text(separator=' ', strip=True)
            else:
                text = soup.get_text(separator=' ', strip=True)
            
            # Filter out common noise patterns
            noise_patterns = [
                r'cookie',
                r'accept.*cookie',
                r'subscribe',
                r'sign up',
                r'newsletter',
                r'follow us',
                r'share on',
                r'click here',
                r'read more',
                r'continue reading',
                r'advertisement',
                r'advert',
                r'privacy policy',
                r'terms of service',
                r'remember me',
                r'forgot password',
                r'log in',
                r'sign in',
                r'create account',
            ]
            
            # Split into sentences/lines and filter
            lines = text.split('. ')
            filtered_lines = []
            for line in lines:
                line_lower = line.lower()
                # Skip lines that are mostly noise
                if any(re.search(pattern, line_lower) for pattern in noise_patterns):
                    continue
                # Skip very short lines (likely navigation items)
                if len(line.strip()) < 10:
                    continue
                # Skip lines that are mostly punctuation/special chars
                if len(re.sub(r'[^\w\s]', '', line)) < len(line) * 0.3:
                    continue
                filtered_lines.append(line)
            
            text = '. '.join(filtered_lines)
            
            # Clean up excessive whitespace
            text = re.sub(r'\s+', ' ', text)
            text = text.strip()
            
            # Limit length
            if len(text) > max_length:
                text = text[:max_length] + "..."
            
            self.logger.debug(f"Fetched {len(text)} chars from {url} (filtered from {len(soup.get_text())} raw chars)")
            return text
            
        except Exception as e:
            self.logger.error(f"Error fetching URL {url}: {e}")
            return None
    
    def search_injury_reports(self, team_name: str, sport: str = "basketball") -> List[Dict[str, Any]]:
        """
        Search for injury reports for a specific team
        
        Args:
            team_name: Name of the team
            sport: Sport type (basketball, football, etc.)
            
        Returns:
            List of injury report information
        """
        query = f"{team_name} {sport} injury report"
        results = self.search_web(query, max_results=3)
        
        # Try to fetch and extract injury info from top results
        injury_info = []
        for result in results[:2]:  # Check top 2 results
            content = self.fetch_url(result['url'], max_length=2000)
            if content:
                # Look for injury-related keywords
                if any(keyword in content.lower() for keyword in ['injury', 'out', 'questionable', 'doubtful', 'probable']):
                    injury_info.append({
                        'source': result['title'],
                        'url': result['url'],
                        'snippet': result['snippet'],
                        'content': content[:500]  # First 500 chars
                    })
        
        return injury_info
    
    def search_team_stats(self, team_name: str, sport: str = "basketball") -> List[Dict[str, Any]]:
        """
        Search for team statistics, including advanced stats from KenPom/Torvik
        
        Args:
            team_name: Name of the team
            sport: Sport type
            
        Returns:
            List of stats information
        """
        # Try multiple search strategies to find advanced stats
        queries = [
            f"{team_name} {sport} kenpom",
            f"{team_name} {sport} torvik",
            f"{team_name} {sport} advanced stats",
            f"{team_name} {sport} adjusted offense defense",
            f"{team_name} {sport} stats recent games"
        ]
        
        all_results = []
        seen_urls = set()
        
        # Search with different queries to find KenPom/Torvik pages
        for query in queries[:3]:  # Use first 3 queries to prioritize KenPom/Torvik
            results = self.search_web(query, max_results=5)
            
            for result in results:
                url = result.get('url', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(result)
        
        stats_info = []
        # Prioritize KenPom/Torvik URLs
        kenpom_torvik_urls = []
        other_urls = []
        
        for result in all_results:
            url = result.get('url', '').lower()
            if 'kenpom' in url or 'barttorvik' in url or 'torvik' in url:
                kenpom_torvik_urls.append(result)
            else:
                other_urls.append(result)
        
        # Process KenPom/Torvik URLs first with more content
        for result in kenpom_torvik_urls[:1]:  # Reduced from 2 to 1 result
            content = self.fetch_url(result['url'], max_length=3000)  # Reduced from 5000 to 3000
            if content:
                stats_info.append({
                    'source': result['title'],
                    'url': result['url'],
                    'snippet': result['snippet'],
                    'content': content[:1000],  # Reduced from 2000 to 1000 chars
                    'is_advanced_stats': True
                })
        
        # Then process other URLs
        for result in other_urls[:1]:  # Reduced from 2 to 1 result
            content = self.fetch_url(result['url'], max_length=1500)  # Reduced from 2000 to 1500
            if content:
                stats_info.append({
                    'source': result['title'],
                    'url': result['url'],
                    'snippet': result['snippet'],
                    'content': content[:500],
                    'is_advanced_stats': False
                })
        
        return stats_info
    
    def search_advanced_stats(self, team_name: str, sport: str = "basketball", target_date: Optional[date] = None) -> List[Dict[str, Any]]:
        """
        Search specifically for advanced statistics (KenPom, Bart Torvik) for a team
        
        This method is optimized to find KenPom and Bart Torvik pages which contain
        advanced metrics like AdjO, AdjD, AdjT, efficiency ratings, and rankings.
        
        If KenPom credentials are configured, this will use direct authenticated access
        for more reliable and complete data extraction.
        
        Args:
            team_name: Name of the team (e.g., "Georgia Bulldogs", "Duke")
            sport: Sport type (default: "basketball")
            target_date: Date to get stats for (defaults to today). Used for cache date matching.
            
        Returns:
            List of advanced stats information with full content from KenPom/Torvik pages
        """
        advanced_stats_info = []
        
        # FIRST: Try authenticated KenPom access if available
        if self.kenpom_scraper and self.kenpom_scraper.is_authenticated():
            try:
                self.logger.info(f"Using authenticated KenPom access for {team_name}")
                kenpom_stats = self.kenpom_scraper.get_team_stats(team_name, target_date=target_date)
                
                if kenpom_stats:
                    # Format KenPom stats as content string for LLM processing
                    # IMPORTANT: Conference must be prominently displayed and accurate
                    content_parts = []
                    # Put conference FIRST to ensure it's seen and used correctly
                    if 'conference' in kenpom_stats:
                        content_parts.append(f"Conference: {kenpom_stats['conference']} (VERIFIED FROM KENPOM)")
                    if 'kenpom_rank' in kenpom_stats:
                        content_parts.append(f"Rank: {kenpom_stats['kenpom_rank']}")
                    if 'w_l' in kenpom_stats:
                        content_parts.append(f"W-L: {kenpom_stats['w_l']}")
                    if 'wins' in kenpom_stats and 'losses' in kenpom_stats:
                        content_parts.append(f"Record: {kenpom_stats['wins']}-{kenpom_stats['losses']}")
                    if 'net_rating' in kenpom_stats:
                        content_parts.append(f"NetRtg: {kenpom_stats['net_rating']}")
                    if 'adj_offense' in kenpom_stats:
                        content_parts.append(f"ORtg (AdjO): {kenpom_stats['adj_offense']}")
                    if 'adj_defense' in kenpom_stats:
                        content_parts.append(f"DRtg (AdjD): {kenpom_stats['adj_defense']}")
                    if 'adj_tempo' in kenpom_stats:
                        content_parts.append(f"AdjT: {kenpom_stats['adj_tempo']}")
                    if 'luck' in kenpom_stats:
                        content_parts.append(f"Luck: {kenpom_stats['luck']}")
                    if 'sos' in kenpom_stats:
                        content_parts.append(f"SOS: {kenpom_stats['sos']}")
                    if 'ncsos' in kenpom_stats:
                        content_parts.append(f"NCSOS: {kenpom_stats['ncsos']}")
                    # Four factors (if available from team page)
                    if 'efg_pct' in kenpom_stats:
                        content_parts.append(f"eFG%: {kenpom_stats['efg_pct']}%")
                    if 'turnover_pct' in kenpom_stats:
                        content_parts.append(f"TO%: {kenpom_stats['turnover_pct']}%")
                    if 'off_reb_pct' in kenpom_stats:
                        content_parts.append(f"OR%: {kenpom_stats['off_reb_pct']}%")
                    if 'ft_rate' in kenpom_stats:
                        content_parts.append(f"FTR: {kenpom_stats['ft_rate']}")
                    
                    content = "\n".join(content_parts) if content_parts else "KenPom stats available"
                    
                    advanced_stats_info.append({
                        'source': f"KenPom.com - {team_name}",
                        'url': f"https://kenpom.com/team.php?team={team_name.replace(' ', '%20')}",
                        'snippet': f"Authenticated KenPom data for {team_name}",
                        'content': content,
                        'is_kenpom': True,
                        'is_torvik': False,
                        'is_advanced_stats': True,
                        'raw_stats': kenpom_stats  # Include raw stats for programmatic access
                    })
                    
                    self.logger.info(f"✓ Successfully retrieved KenPom stats for {team_name}")
            except Exception as e:
                self.logger.warning(f"Error fetching authenticated KenPom data: {e}, falling back to web search")
        
        # FALLBACK: Use web search if KenPom scraper not available or failed
        if not advanced_stats_info:
            # Try multiple search queries specifically targeting KenPom/Torvik
            queries = [
                f"{team_name} {sport} kenpom",
                f"{team_name} {sport} bart torvik",
                f"{team_name} {sport} torvik",
                f"{team_name} kenpom.com",
                f"kenpom {team_name} {sport}",
                f"barttorvik.com {team_name}"
            ]
            
            all_results = []
            seen_urls = set()
            
            # Search with different queries
            for query in queries:
                results = self.search_web(query, max_results=5)
                
                for result in results:
                    url = result.get('url', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(result)
            
            # Filter and prioritize KenPom/Torvik URLs
            kenpom_torvik_results = []
            
            for result in all_results:
                url = result.get('url', '').lower()
                title = result.get('title', '').lower()
                snippet = result.get('snippet', '').lower()
                
                # Check if this is a KenPom or Torvik page
                is_kenpom = 'kenpom' in url or 'kenpom' in title
                is_torvik = 'torvik' in url or 'barttorvik' in url or 'torvik' in title
                
                if is_kenpom or is_torvik:
                    kenpom_torvik_results.append((result, is_kenpom, is_torvik))
            
            # Process KenPom/Torvik pages with full content extraction
            for result, is_kenpom, is_torvik in kenpom_torvik_results[:1]:  # Reduced from 3 to 1 result
                url = result['url']
                self.logger.info(f"Fetching advanced stats from {'KenPom' if is_kenpom else 'Torvik'}: {url}")
                
                # Fetch content for KenPom/Torvik pages (reduced size to save tokens)
                content = self.fetch_url(url, max_length=3000)  # Reduced from 8000 to 3000
                if content:
                    advanced_stats_info.append({
                        'source': result['title'],
                        'url': url,
                        'snippet': result['snippet'],
                        'content': content[:1500],  # Limit to 1500 chars (reduced from full content)
                        'is_kenpom': is_kenpom,
                        'is_torvik': is_torvik,
                        'is_advanced_stats': True
                    })
            
            # If no KenPom/Torvik pages found, try broader advanced stats searches
            if not advanced_stats_info:
                self.logger.warning(f"No KenPom/Torvik pages found for {team_name}, trying broader search")
                fallback_queries = [
                    f"{team_name} {sport} adjusted offense defense",
                    f"{team_name} {sport} efficiency ratings",
                    f"{team_name} {sport} advanced metrics"
                ]
                
                for query in fallback_queries[:1]:  # Reduced from 2 to 1 query
                    results = self.search_web(query, max_results=2)  # Reduced from 3 to 2
                    for result in results[:1]:
                        content = self.fetch_url(result['url'], max_length=2000)  # Reduced from 3000 to 2000
                        if content and any(keyword in content.lower() for keyword in ['adjusted', 'efficiency', 'adj', 'kenpom', 'torvik']):
                            advanced_stats_info.append({
                                'source': result['title'],
                                'url': result['url'],
                                'snippet': result['snippet'],
                                'content': content[:1000],  # Reduced from 2000 to 1000
                                'is_kenpom': False,
                                'is_torvik': False,
                                'is_advanced_stats': True
                            })
                            break
        
        self.logger.info(f"Found {len(advanced_stats_info)} advanced stats sources for {team_name}")
        return advanced_stats_info
    
    def search_game_predictions(self, team1: str, team2: str, sport: str = "basketball", game_date: Optional[date] = None) -> List[Dict[str, Any]]:
        """
        Search for game prediction articles and expert picks
        
        Args:
            team1: First team name
            team2: Second team name
            sport: Sport type (default: "basketball")
            game_date: Target game date for verification (optional but recommended)
            
        Returns:
            List of prediction articles with content and date verification
        """
        # Normalize team names using centralized normalization
        from src.utils.team_normalizer import normalize_team_name_for_lookup
        
        team1_normalized = normalize_team_name_for_lookup(team1)
        team2_normalized = normalize_team_name_for_lookup(team2)
        
        # Include date in search query if provided
        date_str = ""
        if game_date:
            # Format date for search (e.g., "November 18, 2025" or "11-18-2025")
            date_str = f" {game_date.strftime('%B %d, %Y')}"
            date_str_short = f" {game_date.strftime('%m-%d-%Y')}"
        else:
            date_str_short = ""
        
        # For basketball, use more specific terms to avoid football results
        sport_terms = []
        if sport.lower() == "basketball":
            sport_terms = ["NCAAB", "college basketball", "men's college basketball", "NCAA basketball"]
        else:
            sport_terms = [sport]
        
        # Try multiple search queries to find prediction articles
        # Prioritize queries with sport-specific terms
        queries = []
        for sport_term in sport_terms[:2]:  # Use first 2 sport terms
            queries.extend([
                f"{team1_normalized} vs {team2_normalized}{date_str} {sport_term} prediction",
                f"{team1_normalized} vs {team2_normalized}{date_str} {sport_term} pick",
                f"{team1_normalized} vs {team2_normalized} {sport_term} prediction{date_str}",
                f"{team1_normalized} vs {team2_normalized} {sport_term} expert pick{date_str}",
                f"{team1_normalized} vs {team2_normalized}{date_str_short} {sport_term}",
            ])
        
        # Also try with full team names but prioritize sport-specific terms
        if sport.lower() == "basketball":
            queries.extend([
                f"{team1} vs {team2}{date_str} NCAAB prediction",
                f"{team1} vs {team2}{date_str} college basketball pick",
            ])
        
        all_results = []
        seen_urls = set()
        
        # Search with different queries to get variety
        for query in queries[:4]:  # Use first 4 queries to get good coverage
            results = self.search_web(query, max_results=5)
            
            for result in results:
                url = result.get('url', '').lower()
                title = result.get('title', '').lower()
                snippet = result.get('snippet', '').lower()
                
                # Prioritize basketball-related results
                basketball_keywords = ['basketball', 'ncaab', 'nba', 'hoops', 'cbb']
                is_basketball = any(keyword in url or keyword in title or keyword in snippet for keyword in basketball_keywords)
                
                # Prioritize known good sources
                good_sources = ['covers.com', 'espn.com', 'draftkings.com', 'winnersandwhiners.com', 
                               'sportsbookreview.com', 'thescore.com', 'actionnetwork.com']
                is_good_source = any(source in url for source in good_sources)
                
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    # Add priority score for sorting
                    result['_priority'] = (3 if is_good_source else 0) + (2 if is_basketball else 0)
                    all_results.append(result)
        
        # Sort by priority (good sources + basketball content first)
        all_results.sort(key=lambda x: x.get('_priority', 0), reverse=True)
        
        # Fetch and extract content from prediction articles
        prediction_articles = []
        for result in all_results[:3]:  # Reduced from 5 to 3 results to save tokens
            url = result.get('url', '')
            # Fetch more content initially to find the prediction section
            full_content = self.fetch_url(url, max_length=3000)
            if full_content:
                content_lower = full_content.lower()
                
                # Check if content looks like a prediction article
                prediction_keywords = ['prediction', 'pick', 'pick:', 'predicted', 'forecast', 
                                     'winner', 'spread', 'total', 'over/under', 'betting', 
                                     'expert', 'analysis', 'preview', 'odds', 'line']
                if any(keyword in content_lower for keyword in prediction_keywords):
                    # Extract the most relevant portion - look for prediction/pick sections
                    # Try to find sentences/paragraphs that contain prediction keywords
                    sentences = full_content.split('. ')
                    relevant_sentences = []
                    keyword_sentences = []
                    
                    # First pass: collect sentences with prediction keywords (high priority)
                    for sentence in sentences:
                        sentence_lower = sentence.lower()
                        if any(keyword in sentence_lower for keyword in prediction_keywords):
                            keyword_sentences.append(sentence)
                    
                    # Second pass: collect context sentences (near keyword sentences or with numbers/team names)
                    for i, sentence in enumerate(sentences):
                        sentence_lower = sentence.lower()
                        # Keep if it has prediction keywords
                        if any(keyword in sentence_lower for keyword in prediction_keywords):
                            if sentence not in keyword_sentences:
                                keyword_sentences.append(sentence)
                        # Keep if it has numbers (likely scores, stats, odds)
                        elif re.search(r'\d+', sentence):
                            relevant_sentences.append(sentence)
                        # Keep if it mentions teams/game context
                        elif any(word in sentence_lower for word in ['team', 'game', 'matchup', 'vs', 'versus', 'win', 'lose']):
                            relevant_sentences.append(sentence)
                        # Keep sentences immediately before/after keyword sentences (context)
                        elif keyword_sentences and i > 0:
                            prev_sentence = sentences[i-1] if i > 0 else None
                            next_sentence = sentences[i+1] if i < len(sentences)-1 else None
                            if (prev_sentence and prev_sentence in keyword_sentences) or \
                               (next_sentence and next_sentence in keyword_sentences):
                                relevant_sentences.append(sentence)
                    
                    # Combine: keyword sentences first, then relevant context
                    all_relevant = keyword_sentences + relevant_sentences
                    
                    # If we found relevant sentences, use them; otherwise use first portion
                    if all_relevant:
                        # Take up to 12 sentences (increased from 8 to preserve more context)
                        content = '. '.join(all_relevant[:12])
                        if len(content) > 1000:
                            # Try to keep complete sentences
                            last_period = content[:1000].rfind('. ')
                            if last_period > 800:  # Only truncate at sentence boundary if reasonable
                                content = content[:last_period+1] + "..."
                            else:
                                content = content[:1000] + "..."
                    else:
                        # Fallback: use first portion of content
                        content = full_content[:1000]
                    
                    article_data = {
                        'source': result['title'],
                        'url': url,
                        'snippet': result['snippet'],
                        'content': content,
                        'game_date': game_date.isoformat() if game_date else None  # Include game date for reference
                    }
                    prediction_articles.append(article_data)
        
        self.logger.info(
            f"Found {len(prediction_articles)} prediction articles for {team1} vs {team2} "
            f"(search included date: {game_date.isoformat() if game_date else 'none'})"
        )
        return prediction_articles


def get_web_browser() -> WebBrowser:
    """Get a web browser instance"""
    return WebBrowser()

