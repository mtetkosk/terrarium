"""Web browsing utilities for Researcher agent"""

import requests
from typing import Optional, Dict, Any, List
from datetime import date, datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import re
from src.utils.logging import get_logger

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
    
    def search_web(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Search the web using DuckDuckGo (prefers API, falls back to HTML scraping)
        
        Args:
            query: Search query
            max_results: Maximum number of results to return
            
        Returns:
            List of search results with title, url, snippet
        """
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
            
            # Remove duplicates based on URL
            seen_urls = set()
            unique_results = []
            for result in results:
                if result['url'] not in seen_urls:
                    seen_urls.add(result['url'])
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
        Fetch and extract text content from a URL
        
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
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            # Get text
            text = soup.get_text()
            
            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            # Limit length
            if len(text) > max_length:
                text = text[:max_length] + "..."
            
            self.logger.debug(f"Fetched {len(text)} chars from {url}")
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
        Search for team statistics
        
        Args:
            team_name: Name of the team
            sport: Sport type
            
        Returns:
            List of stats information
        """
        query = f"{team_name} {sport} stats recent games"
        results = self.search_web(query, max_results=3)
        
        stats_info = []
        for result in results[:2]:
            content = self.fetch_url(result['url'], max_length=2000)
            if content:
                stats_info.append({
                    'source': result['title'],
                    'url': result['url'],
                    'snippet': result['snippet'],
                    'content': content[:500]
                })
        
        return stats_info
    
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
        # Include date in search query if provided
        date_str = ""
        if game_date:
            # Format date for search (e.g., "January 15, 2025" or "2025-01-15")
            date_str = f" {game_date.strftime('%B %d, %Y')}"
        
        # Try multiple search queries to find prediction articles
        queries = [
            f"{team1} vs {team2}{date_str} {sport} prediction",
            f"{team1} vs {team2}{date_str} {sport} pick",
            f"{team1} vs {team2} {sport} prediction{date_str}",
            f"{team1} vs {team2} {sport} expert pick{date_str}"
        ]
        
        all_results = []
        seen_urls = set()
        
        # Search with different queries to get variety
        for query in queries[:2]:  # Use first 2 queries to avoid too many requests
            results = self.search_web(query, max_results=4)
            
            for result in results:
                url = result.get('url', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(result)
        
        # Fetch and extract content from prediction articles
        prediction_articles = []
        for result in all_results[:3]:  # Limit to top 3 articles
            content = self.fetch_url(result['url'], max_length=3000)
            if content:
                # Check if content looks like a prediction article
                prediction_keywords = ['prediction', 'pick', 'pick:', 'predicted', 'forecast', 
                                     'winner', 'spread', 'total', 'over/under', 'betting', 
                                     'expert', 'analysis', 'preview']
                if any(keyword in content.lower() for keyword in prediction_keywords):
                    article_data = {
                        'source': result['title'],
                        'url': result['url'],
                        'snippet': result['snippet'],
                        'content': content[:2000],  # First 2000 chars of article
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

