"""KenPom scraper for authenticated access to advanced statistics"""

import requests
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup
import re
import time
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from src.utils.logging import get_logger
from src.utils.config import config
from src.utils.team_normalizer import (
    normalize_team_name_for_lookup, 
    normalize_team_name_for_url, 
    get_team_name_variations,
    map_team_name_to_canonical
)
from src.prompts import kenpom_match_prompts

logger = get_logger("scrapers.kenpom")


class KenPomScraper:
    """Scraper for KenPom.com with authentication support"""
    
    BASE_URL = "https://kenpom.com"
    LOGIN_URL = "https://kenpom.com/index.php"
    
    def __init__(self):
        """Initialize KenPom scraper"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        self.authenticated = False
        self.credentials = config.get_kenpom_credentials()
        
        # Cache configuration
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "kenpom_cache.json"
        self.cache_ttl = timedelta(hours=24)  # Cache for 24 hours
        self._team_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_date: Optional[date] = None
        # Four Factors cache: maps team name -> {four_factors: {...}, cache_date: date}
        self._four_factors_cache: Dict[str, Dict[str, Any]] = {}
        
        # Load cache
        self._load_cache()
        
        if self.credentials:
            self._authenticate()
    
    def _authenticate(self) -> bool:
        """Authenticate with KenPom using credentials"""
        if not self.credentials:
            logger.warning("No KenPom credentials provided")
            return False
        
        try:
            # First, get the login page to extract any CSRF tokens or form data
            logger.info("Authenticating with KenPom...")
            login_page = self.session.get(self.LOGIN_URL, timeout=10)
            login_page.raise_for_status()
            
            soup = BeautifulSoup(login_page.text, 'html.parser')
            
            # Find the login form
            form = soup.find('form', {'action': lambda x: x and 'login' in x.lower()}) or soup.find('form')
            
            if not form:
                # Try alternative: KenPom might use a direct POST to index.php
                # Look for login form fields
                pass
            
            # Prepare login data
            login_data = {
                'email': self.credentials['email'],
                'password': self.credentials['password'],
            }
            
            # Try to find and include any hidden form fields
            if form:
                for hidden_input in form.find_all('input', type='hidden'):
                    name = hidden_input.get('name')
                    value = hidden_input.get('value', '')
                    if name:
                        login_data[name] = value
            
            # Attempt login - KenPom typically uses POST to index.php with email/password
            # The exact endpoint may vary, so we'll try a few common patterns
            login_endpoints = [
                self.LOGIN_URL,
                urljoin(self.BASE_URL, '/login.php'),
                urljoin(self.BASE_URL, '/index.php?y=2025'),  # Current season
            ]
            
            for endpoint in login_endpoints:
                try:
                    response = self.session.post(
                        endpoint,
                        data=login_data,
                        allow_redirects=True,
                        timeout=10
                    )
                    
                    # Check if login was successful
                    # KenPom typically redirects or shows different content after login
                    if response.status_code == 200:
                        # Check if we're logged in by looking for subscription-only content
                        # or checking if we can access team pages
                        if self._check_authentication(response.text):
                            self.authenticated = True
                            logger.info("✓ Successfully authenticated with KenPom")
                            return True
                except Exception as e:
                    logger.debug(f"Login attempt to {endpoint} failed: {e}")
                    continue
            
            logger.warning("Failed to authenticate with KenPom - check credentials")
            return False
            
        except Exception as e:
            logger.error(f"Error during KenPom authentication: {e}")
            return False
    
    def _check_authentication(self, html_content: str) -> bool:
        """Check if authentication was successful by looking for indicators"""
        # Look for signs of being logged in:
        # - Subscription-only content
        # - Team detail pages accessible
        # - No "login required" messages
        
        indicators = [
            'team.php',  # Team detail pages
            'AdjO',  # Advanced stats
            'AdjD',
            'AdjT',
            'logout',  # Logout link
        ]
        
        negative_indicators = [
            'please log in',
            'subscription required',
            'login to access',
        ]
        
        content_lower = html_content.lower()
        
        # Check for negative indicators first
        for neg_indicator in negative_indicators:
            if neg_indicator in content_lower:
                return False
        
        # Check for positive indicators
        positive_count = sum(1 for indicator in indicators if indicator.lower() in content_lower)
        return positive_count >= 2
    
    def _load_cache(self) -> None:
        """Load cached team data from file"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                    self._team_cache = cache_data.get('teams', {})
                    cache_date_str = cache_data.get('cache_date')
                    if cache_date_str:
                        self._cache_date = date.fromisoformat(cache_date_str)
                    # Load Four Factors cache
                    self._four_factors_cache = cache_data.get('four_factors', {})
                    # Convert cache_date strings to date objects
                    for team_name, team_data in self._four_factors_cache.items():
                        if 'cache_date' in team_data and isinstance(team_data['cache_date'], str):
                            try:
                                team_data['cache_date'] = date.fromisoformat(team_data['cache_date'])
                            except (ValueError, TypeError):
                                pass
                    logger.info(f"Loaded KenPom cache with {len(self._team_cache)} teams (cached on {self._cache_date}), {len(self._four_factors_cache)} Four Factors entries")
            except Exception as e:
                logger.warning(f"Failed to load KenPom cache: {e}")
                self._team_cache = {}
                self._cache_date = None
                self._four_factors_cache = {}
    
    def _save_cache(self, target_date: Optional[date] = None) -> None:
        """Save team data to cache file for a specific date
        
        Args:
            target_date: Date to save cache for (defaults to today)
        """
        if target_date is None:
            target_date = date.today()
        
        try:
            # Only store one day's data at a time - overwrite previous cache
            # Convert four_factors cache dates to strings for JSON serialization
            four_factors_for_save = {}
            for team_name, team_data in self._four_factors_cache.items():
                team_data_copy = team_data.copy()
                if 'cache_date' in team_data_copy and isinstance(team_data_copy['cache_date'], date):
                    team_data_copy['cache_date'] = team_data_copy['cache_date'].isoformat()
                four_factors_for_save[team_name] = team_data_copy
            
            cache_data = {
                'cache_date': target_date.isoformat(),
                'teams': self._team_cache,
                'four_factors': four_factors_for_save
            }
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            self._cache_date = target_date
            logger.info(f"Saved KenPom cache with {len(self._team_cache)} teams and {len(self._four_factors_cache)} Four Factors entries for {target_date}")
        except Exception as e:
            logger.error(f"Failed to save KenPom cache: {e}")
    
    def _is_cache_for_date(self, target_date: date) -> bool:
        """Check if cache is for the specified date"""
        if not self._cache_date:
            return False
        return self._cache_date == target_date
    
    def _is_cache_stale(self) -> bool:
        """Check if cache is stale based on TTL"""
        if not self._cache_date:
            return True  # No cache is considered stale
        age = date.today() - self._cache_date
        return age > self.cache_ttl
    
    def _refresh_homepage_cache(self, target_date: Optional[date] = None) -> bool:
        """
        Scrape KenPom homepage once and cache all team data for a specific date
        
        Args:
            target_date: Date to cache data for (defaults to today)
        
        Returns:
            True if successful, False otherwise
        """
        if target_date is None:
            target_date = date.today()
        
        if not self.authenticated:
            logger.warning("Not authenticated with KenPom, cannot refresh cache")
            return False
        
        try:
            logger.info(f"Scraping KenPom homepage to refresh cache for {target_date}...")
            
            # Get season year based on target_date
            if target_date.month < 10:  # Before October, use current year
                season_year = target_date.year
            else:
                season_year = target_date.year + 1
            
            homepage_url = f"{self.BASE_URL}/index.php?y={season_year}"
            response = self.session.get(homepage_url, timeout=10)
            response.raise_for_status()
            
            # Parse homepage table
            teams_data = self._parse_homepage_table(response.text)
            
            if teams_data:
                self._team_cache = teams_data
                self._save_cache(target_date)
                logger.info(f"✓ Successfully cached {len(teams_data)} teams from KenPom homepage for {target_date}")
                return True
            else:
                logger.warning("Failed to parse teams from KenPom homepage")
                return False
                
        except Exception as e:
            logger.error(f"Error refreshing KenPom homepage cache: {e}")
            return False
    
    def _parse_homepage_table(self, html: str) -> Dict[str, Dict[str, Any]]:
        """
        Parse the main rankings table from KenPom homepage
        
        Returns:
            Dictionary mapping team names to their stats
        """
        teams_data = {}
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find the main rankings table
            table = soup.find('table', id='ratings-table') or soup.find('table')
            
            if not table:
                logger.warning("Could not find rankings table on KenPom homepage")
                return teams_data
            
            # Find header row to identify column indices
            # KenPom has multiple header rows - we need row 1 (index 1) which has the actual column names
            # Row 0 has mostly empty cells with some merged headers like "Strength of Schedule"
            all_rows = table.find_all('tr')
            if len(all_rows) < 2:
                return teams_data
            
            # Get the main header row (usually row 1, index 1)
            # Try to find row with "Rk" or "Team" as it should have the main column names
            header_row = None
            for row in all_rows[:3]:  # Check first 3 rows
                headers_test = [th.get_text(strip=True).lower() for th in row.find_all(['th', 'td'])]
                if 'rk' in headers_test or 'team' in headers_test:
                    header_row = row
                    break
            
            if not header_row:
                # Fallback to first row
                header_row = all_rows[0]
            
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])]
            
            # Debug: Log headers found
            logger.debug(f"Found {len(headers)} columns in KenPom table: {headers}")
            
            # Find column indices for key stats
            # KenPom table columns: Rk, Team, Conf, W-L, NetRtg, ORtg, DRtg, AdjT, Luck, SOS columns, NCSOS
            col_indices = {}
            drtg_occurrence_count = 0  # Track DRtg occurrences
            for i, header in enumerate(headers):
                header_lower = header.lower().strip()
                # Rank column (usually first)
                if header_lower == 'rk' or header_lower == 'rank':
                    col_indices['rank'] = i
                # Conference
                elif header_lower in ['conf', 'conference', 'conf.']:
                    col_indices['conference'] = i
                # Win-Loss record
                elif header_lower in ['w-l', 'wl', 'record', 'w-l record']:
                    col_indices['wl'] = i
                # Offensive Rating (ORtg - take first occurrence, column 5)
                elif header_lower == 'ortg' and 'adj_offense' not in col_indices:
                    col_indices['adj_offense'] = i
                    # Don't store duplicate 'ortg' key - only use 'adj_offense'
                # Defensive Rating (DRtg) - the AdjD value is in the first DRtg column
                # KenPom might label it as "DRtg", "DRtg.", "Def", "Def.", etc.
                elif header_lower in ['drtg', 'drtg.', 'def', 'def.', 'defensive', 'defensive rating']:
                    if '_drtg_first_col' not in col_indices:
                        col_indices['_drtg_first_col'] = i
                # Adjusted Tempo (AdjT)
                # In simple structure: AdjT column contains AdjT value
                # In complex structure: AdjT column header contains DRtg value, actual AdjT is in second NetRtg
                elif header_lower == 'adjt' or 'tempo' in header_lower:
                    # Store this column index
                    # In simple structure, this will be used for AdjT
                    # In complex structure, this column contains DRtg value (we'll use second NetRtg for AdjT)
                    col_indices['_adjt_header_col'] = i
                    # If we don't have complex structure yet, assume simple structure and use this for AdjT
                    if not col_indices.get('_has_complex_structure', False):
                        col_indices['adj_tempo'] = i
                # Luck - always store the Luck header column index
                # The Luck value is in the column after the Luck header (header = rank, next = value)
                elif header_lower == 'luck':
                    col_indices['luck'] = i
                # NetRtg occurrences:
                # - First NetRtg (column 4) = Net Rating value
                # - Second NetRtg (column 9) = AdjT value (not NetRtg!)
                # - Third NetRtg (column 12) = NCSOS value (not NetRtg!)
                elif 'netrtg' in header_lower:
                    netrtg_count = col_indices.get('_netrtg_count', 0)
                    netrtg_count += 1
                    col_indices['_netrtg_count'] = netrtg_count
                    if netrtg_count == 1:
                        # First occurrence - this is the actual Net Rating
                        col_indices['net_rating'] = i
                    elif netrtg_count == 2:
                        # Second occurrence - this is actually AdjT, not NetRtg! (complex structure)
                        col_indices['adj_tempo'] = i
                        # Override simple structure assignment if we have complex structure
                        col_indices['_has_complex_structure'] = True
                    elif netrtg_count == 3:
                        # Third occurrence - this is actually NCSOS, not NetRtg!
                        col_indices['ncsos'] = i
                        # Store this index so we can find SOS (which is 1 column after NCSOS)
                        col_indices['_ncsos_col'] = i
            
            # Debug: Log column indices found
            logger.debug(f"Column indices found: {col_indices}")
            
            # Parse data rows
            # Skip header rows (first 2 rows are headers in KenPom)
            all_rows = table.find_all('tr')
            rows = []
            header_row_count = 0
            for i, row in enumerate(all_rows):
                cells = row.find_all(['td', 'th'])
                first_cell = cells[0].get_text(strip=True).lower() if cells else ""
                # If first cell is a number (rank), it's a data row
                if first_cell.isdigit():
                    rows.append(row)
                elif i < 2:  # First 2 rows are headers
                    header_row_count += 1
            
            # If we didn't find any data rows, fall back to skipping first 2 rows
            if not rows:
                rows = all_rows[2:] if len(all_rows) > 2 else all_rows[1:]
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 4:
                    continue
                
                try:
                    # Extract team name (usually in a link)
                    team_cell = cells[1] if len(cells) > 1 else cells[0]
                    team_link = team_cell.find('a')
                    if team_link:
                        team_name = team_link.get_text(strip=True)
                    else:
                        team_name = team_cell.get_text(strip=True)
                    
                    if not team_name:
                        continue
                    
                    # Normalize and map team name to canonical form
                    # Step 1: Normalize
                    normalized_name = normalize_team_name_for_lookup(team_name)
                    # Step 2: Map to canonical form
                    canonical_name = map_team_name_to_canonical(team_name)
                    
                    # Extract rank (first column usually)
                    rank_text = cells[0].get_text(strip=True)
                    rank = None
                    if rank_text.isdigit():
                        rank = int(rank_text)
                    
                    # Build team stats dict with all metrics
                    # Store original KenPom name in 'team' field for reference
                    team_stats = {
                        'team': team_name,  # Original KenPom name
                        'source': 'kenpom',
                        'kenpom_rank': rank,
                    }
                    
                    # Extract numeric stats based on column indices
                    def safe_float(value_str):
                        """Safely convert string to float"""
                        if not value_str:
                            return None
                        # Remove non-numeric characters except decimal point and minus sign
                        cleaned = re.sub(r'[^\d.\-+]', '', value_str)
                        try:
                            return float(cleaned)
                        except (ValueError, TypeError):
                            return None
                    
                    def safe_int(value_str):
                        """Safely convert string to int"""
                        if not value_str:
                            return None
                        cleaned = re.sub(r'[^\d\-+]', '', value_str)
                        try:
                            return int(cleaned)
                        except (ValueError, TypeError):
                            return None
                    
                    # Extract all stats by column index
                    if 'conference' in col_indices:
                        idx = col_indices['conference']
                        if idx < len(cells):
                            conf_text = cells[idx].get_text(strip=True)
                            if conf_text:
                                team_stats['conference'] = conf_text
                    
                    if 'wl' in col_indices:
                        idx = col_indices['wl']
                        if idx < len(cells):
                            wl_text = cells[idx].get_text(strip=True)
                            # Parse W-L record (e.g., "4-0", "12-3")
                            wl_match = re.match(r'(\d+)-(\d+)', wl_text)
                            if wl_match:
                                team_stats['wins'] = int(wl_match.group(1))
                                team_stats['losses'] = int(wl_match.group(2))
                                # Don't store w_l - we already have wins and losses
                    
                    if 'net_rating' in col_indices:
                        idx = col_indices['net_rating']
                        if idx < len(cells):
                            team_stats['net_rating'] = safe_float(cells[idx].get_text(strip=True))
                    
                    if 'adj_offense' in col_indices:
                        idx = col_indices['adj_offense']
                        if idx < len(cells):
                            team_stats['adj_offense'] = safe_float(cells[idx].get_text(strip=True))
                            # Don't create duplicate 'ortg' field - only use 'adj_offense'
                    
                    # AdjD parsing - the AdjD value is in the first DRtg column
                    # In KenPom's table: Rank, Team, Conf, W-L, NetRtg, AdjO, AdjO Rank, AdjD, AdjD Rank, ...
                    # So AdjD is typically 2 columns after AdjO (AdjO, AdjO Rank, AdjD)
                    adjd_value = None
                    drtg_first_col = col_indices.get('_drtg_first_col')
                    
                    # Method 1: Try the DRtg column if we found it
                    if drtg_first_col is not None and drtg_first_col < len(cells):
                        test_value = safe_float(cells[drtg_first_col].get_text(strip=True))
                        if test_value is not None and 70 <= test_value <= 130:
                            adjd_value = test_value
                    
                    # Method 2: If DRtg column not found or value invalid, infer from AdjO position
                    if adjd_value is None and 'adj_offense' in col_indices:
                        adjo_col = col_indices['adj_offense']
                        # AdjD is typically 2 columns after AdjO (AdjO, AdjO Rank, AdjD)
                        adjd_col = adjo_col + 2
                        if adjd_col < len(cells):
                            test_value = safe_float(cells[adjd_col].get_text(strip=True))
                            if test_value is not None and 70 <= test_value <= 130:
                                adjd_value = test_value
                                logger.debug(f"Found AdjD value {adjd_value} for {team_name} by inferring from AdjO position (column {adjd_col})")
                    
                    if adjd_value is not None:
                        team_stats['adj_defense'] = adjd_value
                    else:
                        logger.warning(
                            f"Could not find AdjD value for {team_name}. "
                            f"DRtg col: {drtg_first_col}, AdjO col: {col_indices.get('adj_offense')}"
                        )
                    
                    if 'adj_tempo' in col_indices:
                        idx = col_indices['adj_tempo']
                        if idx < len(cells):
                            # AdjT is at the second NetRtg column (column 9), not the AdjT header column (column 7)
                            team_stats['adj_tempo'] = safe_float(cells[idx].get_text(strip=True))
                            # Don't create duplicate 'adjt' field - only use 'adj_tempo'
                    
                    # Luck parsing - in KenPom table: AdjT, AdjT Rank, Luck, Luck Rank, ...
                    # So Luck is 2 columns after AdjT (AdjT, AdjT Rank, Luck)
                    luck_value = None
                    
                    # Method 1: Try the Luck column if we found it
                    if 'luck' in col_indices:
                        idx = col_indices['luck']
                        # The Luck value is in the column after the Luck header
                        # (Luck header column = rank, next column = actual luck value)
                        if idx + 1 < len(cells):
                            test_value = safe_float(cells[idx + 1].get_text(strip=True))
                            # Luck values are small decimals like 0.037, not large numbers like 70.0 or 113
                            if test_value is not None and -0.5 <= test_value <= 0.5:
                                luck_value = test_value
                    
                    # Method 2: If Luck column not found or value invalid, infer from AdjT position
                    if luck_value is None and 'adj_tempo' in col_indices:
                        adjt_col = col_indices['adj_tempo']
                        # Luck is 2 columns after AdjT (AdjT, AdjT Rank, Luck)
                        luck_col = adjt_col + 2
                        if luck_col < len(cells):
                            test_value = safe_float(cells[luck_col].get_text(strip=True))
                            if test_value is not None and -0.5 <= test_value <= 0.5:
                                luck_value = test_value
                                logger.debug(f"Found Luck value {luck_value} for {team_name} by inferring from AdjT position (column {luck_col})")
                    
                    if luck_value is not None:
                        team_stats['luck'] = luck_value
                    else:
                        logger.warning(
                            f"Could not find Luck value for {team_name}. "
                            f"Luck col: {col_indices.get('luck')}, AdjT col: {col_indices.get('adj_tempo')}"
                        )
                    
                    # SOS (Strength of Schedule) - in unlabeled column 1 after third NetRtg
                    # Note: third NetRtg column contains NCSOS, but we're removing NCSOS from output
                    ncsos_col = col_indices.get('_ncsos_col')
                    if ncsos_col is not None:
                        sos_idx = ncsos_col + 1  # SOS is 1 column after third NetRtg
                        if sos_idx < len(cells):
                            sos_value = safe_float(cells[sos_idx].get_text(strip=True))
                            if sos_value is not None:
                                team_stats['sos'] = sos_value
                                logger.debug(f"Found SOS value {sos_value} for {team_name} at column {sos_idx} (1 after third NetRtg at {ncsos_col})")
                    
                    # Don't store ncsos - user requested removal
                    
                    # Store by canonical name (normalized + mapped)
                    # This ensures consistent naming and avoids duplicates
                    # normalized_name and canonical_name were computed above
                    teams_data[canonical_name] = team_stats
                    
                    # Also store normalized name as alias if different from canonical
                    # This allows faster lookup for common variations
                    if normalized_name != canonical_name and normalized_name not in teams_data:
                        teams_data[normalized_name] = team_stats
                    
                except Exception as e:
                    logger.debug(f"Error parsing team row: {e}")
                    continue
            
            return teams_data
            
        except Exception as e:
            logger.error(f"Error parsing KenPom homepage table: {e}")
            return {}
    
    
    def get_team_stats(self, team_name: str, target_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """
        Get team statistics from KenPom (from cache if available)
        
        Args:
            team_name: Name of the team (e.g., "Duke", "Georgia")
            target_date: Date to get stats for (defaults to today). Cache refreshes if date doesn't match.
            
        Returns:
            Dictionary with team stats or None if error
        """
        if target_date is None:
            target_date = date.today()
        
        # Check if cache is for the correct date - refresh if not matching
        if not self._is_cache_for_date(target_date) or not self._team_cache:
            if self.authenticated:
                if not self._is_cache_for_date(target_date):
                    logger.info(f"KenPom cache is for {self._cache_date}, but need {target_date}. Refreshing...")
                else:
                    logger.info("KenPom cache is empty, refreshing from homepage...")
                
                if not self._refresh_homepage_cache(target_date):
                    logger.warning("Failed to refresh KenPom cache")
            else:
                logger.debug("Not authenticated, skipping KenPom cache refresh (will use existing cache)")
        
        # If cache is still empty, we can't do anything
        if not self._team_cache:
            if not self.authenticated:
                logger.warning("No KenPom cache available and not authenticated")
            return None
        
        # CRITICAL: Normalize input team name FIRST before any matching
        # This ensures matching happens on normalized names on BOTH sides
        # Step 1: Normalize (removes mascots, standardizes format)
        normalized = normalize_team_name_for_lookup(team_name)
        # Step 2: Map to canonical form
        canonical_name = map_team_name_to_canonical(team_name)
        
        logger.debug(f"Looking up team '{team_name}' -> normalized: '{normalized}', canonical: '{canonical_name}'")
        logger.debug(f"Cache has {len(self._team_cache)} teams. Sample keys: {list(self._team_cache.keys())[:5]}")
        
        # Step 3: Try direct lookup with canonical name (primary lookup)
        # Cache keys are already normalized from cache building, so this matches normalized to normalized
        if canonical_name in self._team_cache:
            stats = self._team_cache[canonical_name].copy()
            logger.debug(f"Found KenPom stats for '{team_name}' (normalized: '{normalized}') via canonical name: '{canonical_name}'")
            
            # DISABLED: Four Factors fetching to avoid rate limiting
            # To re-enable, uncomment the code below
            # # Fetch Four Factors if not already in stats and authenticated
            # # Check if any Four Factors are missing
            # four_factors_keys = ['efg_pct', 'turnover_pct', 'off_reb_pct', 'fta_per_fga']
            # has_four_factors = any(key in stats for key in four_factors_keys)
            # 
            # if self.authenticated and not has_four_factors:
            #     # Get original KenPom team name for fetching
            #     original_team_name = stats.get('team', team_name)
            #     four_factors = self._get_four_factors_from_team_page(original_team_name, target_date)
            #     if four_factors:
            #         stats.update(four_factors)
            
            return stats
        
        # Step 4: Try normalized name (in case it's stored as alias)
        if normalized in self._team_cache:
            stats = self._team_cache[normalized].copy()
            logger.debug(f"Found KenPom stats for '{team_name}' (normalized: '{normalized}') via normalized name")
            
            # DISABLED: Four Factors fetching to avoid rate limiting
            # To re-enable, uncomment the code below
            # if self.authenticated and not any(key.startswith('efg_pct') or key.startswith('turnover_pct') or 
            #                                  key.startswith('off_reb_pct') or key.startswith('fta_per_fga') for key in stats.keys()):
            #     original_team_name = stats.get('team', team_name)
            #     four_factors = self._get_four_factors_from_team_page(original_team_name, target_date)
            #     if four_factors:
            #         stats.update(four_factors)
            
            return stats
        
        # Step 5: Try all variations (for backwards compatibility with old cache entries)
        lookup_names = get_team_name_variations(team_name)
        for lookup_name in lookup_names:
            # Normalize and map each variation too
            lookup_normalized = normalize_team_name_for_lookup(lookup_name)
            lookup_canonical = map_team_name_to_canonical(lookup_name)
            
            if lookup_canonical in self._team_cache:
                stats = self._team_cache[lookup_canonical].copy()
                logger.debug(f"Found KenPom stats for '{team_name}' (normalized: '{normalized}') via variation canonical: '{lookup_canonical}'")
                
                # DISABLED: Four Factors fetching to avoid rate limiting
                # To re-enable, uncomment the code below
                # if self.authenticated and not any(key.startswith('efg_pct') or key.startswith('turnover_pct') or 
                #                                  key.startswith('off_reb_pct') or key.startswith('fta_per_fga') for key in stats.keys()):
                #     original_team_name = stats.get('team', team_name)
                #     four_factors = self._get_four_factors_from_team_page(original_team_name, target_date)
                #     if four_factors:
                #         stats.update(four_factors)
                
                return stats
            if lookup_normalized in self._team_cache:
                stats = self._team_cache[lookup_normalized].copy()
                logger.debug(f"Found KenPom stats for '{team_name}' (normalized: '{normalized}') via variation normalized: '{lookup_normalized}'")
                
                # DISABLED: Four Factors fetching to avoid rate limiting
                # To re-enable, uncomment the code below
                # if self.authenticated and not any(key.startswith('efg_pct') or key.startswith('turnover_pct') or 
                #                                  key.startswith('off_reb_pct') or key.startswith('fta_per_fga') for key in stats.keys()):
                #     original_team_name = stats.get('team', team_name)
                #     four_factors = self._get_four_factors_from_team_page(original_team_name, target_date)
                #     if four_factors:
                #         stats.update(four_factors)
                
                return stats
        
        # If still not found, log warning with more details to help debug
        logger.warning(
            f"Could not find KenPom stats for '{team_name}' (normalized: '{normalized}', canonical: '{canonical_name}') in cache. "
            f"Cache has {len(self._team_cache)} teams. "
            f"Checking if '{normalized}' is in cache: {normalized in self._team_cache}. "
            f"Skipping fuzzy match to avoid hallucinations."
        )
        return None
    
    def _llm_fuzzy_match_team(self, team_name: str) -> Optional[str]:
        """
        DEPRECATED: Use LLM to fuzzy match a team name to a KenPom team name.
        
        This method is deprecated because it can produce hallucinations (e.g. matching "Iowa Hawkeyes" to a random rank).
        It is better to fail and log a warning than to return incorrect data.
        
        Args:
            team_name: Team name to match
            
        Returns:
            None (always returns None to disable this feature)
        """
        return None
    
    def _find_team_url(self, team_name: str) -> Optional[str]:
        """Find the KenPom URL for a team by searching the teams page"""
        try:
            # First, try direct URL construction (most efficient)
            # KenPom uses team names like "Duke", "North Carolina", etc.
            # Handle common team name variations
            team_id = normalize_team_name_for_url(team_name)
            direct_url = urljoin(self.BASE_URL, f"/team.php?team={team_id}")
            
            # Test if the direct URL works by making a quick HEAD request
            try:
                response = self.session.head(direct_url, timeout=5, allow_redirects=True)
                if response.status_code == 200:
                    logger.debug(f"Direct URL works for {team_name}: {direct_url}")
                    return direct_url
            except:
                pass
            
            # Fallback: Search the teams page
            from datetime import date
            current_year = date.today().year
            # Adjust year for basketball season (2024-25 season = 2025)
            if date.today().month < 10:  # Before October, use previous year
                season_year = current_year
            else:
                season_year = current_year + 1
            
            teams_url = f"{self.BASE_URL}/index.php?y={season_year}"
            response = self.session.get(teams_url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find team links - KenPom uses links like <a href="team.php?team=TeamName">Team Name</a>
            team_links = soup.find_all('a', href=re.compile(r'team\.php\?team='))
            
            # Try to match team name (case-insensitive, partial match)
            team_name_lower = team_name.lower()
            for link in team_links:
                link_text = link.get_text(strip=True).lower()
                href = link.get('href', '')
                
                # Check if team name matches
                if team_name_lower in link_text or link_text in team_name_lower:
                    # Extract team identifier from href
                    match = re.search(r'team=([^&]+)', href)
                    if match:
                        team_id = match.group(1)
                        return urljoin(self.BASE_URL, f"/team.php?team={team_id}")
            
            # Final fallback: return direct URL even if we couldn't verify it
            logger.warning(f"Could not verify team URL for {team_name}, using direct URL")
            return direct_url
            
        except Exception as e:
            logger.warning(f"Error finding team URL for {team_name}: {e}")
            # Fallback: try direct URL construction
            team_id = normalize_team_name_for_url(team_name)
            return urljoin(self.BASE_URL, f"/team.php?team={team_id}")
    
    def _get_four_factors_from_team_page(self, team_name: str, target_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """
        Fetch and cache Four Factors stats from a team's KenPom page
        
        Args:
            team_name: Name of the team (original KenPom name)
            target_date: Date to cache for (defaults to today)
            
        Returns:
            Dictionary with Four Factors stats or None if error
        """
        if target_date is None:
            target_date = date.today()
        
        if not self.authenticated:
            logger.debug("Not authenticated, cannot fetch Four Factors from team page")
            return None
        
        # Normalize team name for cache lookup
        normalized = normalize_team_name_for_lookup(team_name)
        canonical_name = map_team_name_to_canonical(team_name)
        
        # Check cache - try canonical_name first, then normalized
        cache_key = None
        for key in [canonical_name, normalized]:
            if key in self._four_factors_cache:
                cached_data = self._four_factors_cache[key]
                cached_date = cached_data.get('cache_date')
                if isinstance(cached_date, str):
                    try:
                        cached_date = date.fromisoformat(cached_date)
                    except (ValueError, TypeError):
                        cached_date = None
                
                if cached_date == target_date and 'four_factors' in cached_data:
                    logger.debug(f"Using cached Four Factors for {team_name} (cached on {cached_date})")
                    return cached_data['four_factors']
                cache_key = key  # Remember which key was found (for storing later)
                break
        
        # Use canonical_name as cache key for storing (consistent with team_cache)
        cache_key = canonical_name
        
        # Need to fetch from team page
        try:
            logger.info(f"Fetching Four Factors from team page for {team_name}")
            team_url = self._find_team_url(team_name)
            if not team_url:
                logger.warning(f"Could not find team URL for {team_name}")
                return None
            
            response = self.session.get(team_url, timeout=10)
            response.raise_for_status()
            
            # Parse Four Factors from page
            four_factors = self._parse_four_factors_from_page(response.text, team_name)
            
            if four_factors:
                # Cache the result
                self._four_factors_cache[cache_key] = {
                    'four_factors': four_factors,
                    'cache_date': target_date
                }
                self._save_cache(target_date)
                logger.info(f"✓ Cached Four Factors for {team_name}")
                return four_factors
            else:
                logger.warning(f"Could not extract Four Factors for {team_name}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching Four Factors for {team_name}: {e}")
            return None
    
    def _parse_four_factors_from_page(self, html: str, team_name: str) -> Optional[Dict[str, Any]]:
        """
        Parse Four Factors from KenPom team page HTML
        
        Args:
            html: HTML content of the team page
            team_name: Name of the team (for logging)
            
        Returns:
            Dictionary with Four Factors stats or None if not found
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            four_factors = {}
            
            # Find Four Factors table
            # Look for table with "Four Factors" in header or nearby text
            tables = soup.find_all('table')
            
            for table in tables:
                # Check if this is the Four Factors table
                # Look for "Four Factors" text near the table
                table_text = table.get_text().lower()
                prev_sibling_text = ''
                if table.find_previous_sibling():
                    prev_sibling_text = table.find_previous_sibling().get_text().lower()
                
                if 'four factors' in table_text or 'four factors' in prev_sibling_text:
                    # Found Four Factors table
                    rows = table.find_all('tr')
                    
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) < 2:
                            continue
                        
                        # First cell contains the stat name
                        label = cells[0].get_text(strip=True).lower()
                        # Second cell contains the offensive value (and possibly rank)
                        value_cell_text = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                        
                        # Extract numeric value from cell (may contain rank after the value)
                        # Format is typically "51.4 166" where 51.4 is the value and 166 is the rank
                        value_match = re.search(r'(\d+\.?\d*)', value_cell_text)
                        if not value_match:
                            continue
                        
                        try:
                            value = float(value_match.group(1))
                            
                            # Extract four factors based on label
                            if 'efg%' in label or 'effective' in label or 'efg' in label:
                                four_factors['efg_pct'] = value
                                # Also store offensive rank if available
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    four_factors['efg_pct_rank'] = int(rank_match.group(1))
                            elif 'to%' in label or 'turnover' in label or 'turn' in label:
                                four_factors['turnover_pct'] = value
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    four_factors['turnover_pct_rank'] = int(rank_match.group(1))
                            elif 'or%' in label or 'offensive rebound' in label or ('off' in label and 'reb' in label):
                                four_factors['off_reb_pct'] = value
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    four_factors['off_reb_pct_rank'] = int(rank_match.group(1))
                            elif 'fta/fga' in label or 'ftr' in label or 'ft rate' in label or ('ft' in label and 'rate' in label):
                                four_factors['fta_per_fga'] = value
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    four_factors['fta_per_fga_rank'] = int(rank_match.group(1))
                        except (ValueError, TypeError):
                            continue
                    
                    # If we found any Four Factors, return them
                    if four_factors:
                        logger.info(f"Extracted Four Factors for {team_name}: {list(four_factors.keys())}")
                        return four_factors
            
            # If not found in table, try text patterns as fallback
            text = soup.get_text()
            patterns = [
                (r'Effective\s+FG%[:\s.]+(\d+\.?\d*)', 'efg_pct'),
                (r'eFG%[:\s.]+(\d+\.?\d*)', 'efg_pct'),
                (r'Turnover\s+%[:\s.]+(\d+\.?\d*)', 'turnover_pct'),
                (r'TO%[:\s.]+(\d+\.?\d*)', 'turnover_pct'),
                (r'Off\.\s+Reb\.\s+%[:\s.]+(\d+\.?\d*)', 'off_reb_pct'),
                (r'OR%[:\s.]+(\d+\.?\d*)', 'off_reb_pct'),
                (r'FTA/FGA[:\s.]+(\d+\.?\d*)', 'fta_per_fga'),
                (r'FTR[:\s.]+(\d+\.?\d*)', 'fta_per_fga'),
            ]
            
            for pattern, key in patterns:
                if key not in four_factors:  # Only if not already found
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        try:
                            value = float(match.group(1))
                            four_factors[key] = value
                        except (ValueError, TypeError):
                            pass
            
            if four_factors:
                logger.info(f"Extracted Four Factors from text patterns for {team_name}: {list(four_factors.keys())}")
                return four_factors
            
            logger.warning(f"Could not find Four Factors for {team_name}")
            return None
                
        except Exception as e:
            logger.error(f"Error parsing Four Factors from page for {team_name}: {e}")
            return None
    
    def _parse_team_page(self, html: str, team_name: str) -> Optional[Dict[str, Any]]:
        """Parse team statistics from KenPom team page HTML"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            stats = {
                'team': team_name,
                'source': 'kenpom',
            }
            
            # KenPom team pages have multiple sections with stats
            # 1. Main header section with overall ratings
            # 2. Four Factors table
            # 3. Additional stats tables
            
            # Method 1: Parse from the main header/overview section
            # Look for the team name header and nearby stats
            header = soup.find('h2') or soup.find('h1')
            if header:
                header_text = header.get_text()
                # Extract rank from header if present (e.g., "#5 Duke")
                rank_match = re.search(r'#(\d+)', header_text)
                if rank_match:
                    try:
                        stats['kenpom_rank'] = int(rank_match.group(1))
                    except (ValueError, TypeError):
                        pass
            
            # Method 2: Parse all tables systematically
            tables = soup.find_all('table')
            
            for table in tables:
                # Look for table headers to identify table type
                header_row = table.find('tr')
                if not header_row:
                    continue
                
                headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]
                header_text = ' '.join(headers).lower()
                
                # Parse Four Factors table
                # Four Factors table format:
                # Row headers: Stat Name | Offensive Value | Offensive Rank | Defensive Value | Defensive Rank | National Average
                # We want the offensive values (columns 1-2 after stat name)
                if any(keyword in header_text for keyword in ['efg%', 'to%', 'or%', 'ftr', 'fta/fga', 'four factors']):
                    rows = table.find_all('tr')[1:]  # Skip header row
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) < 2:
                            continue
                        
                        # First cell contains the stat name
                        label = cells[0].get_text(strip=True).lower()
                        # Second cell contains the offensive value (and possibly rank)
                        value_cell_text = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                        
                        # Extract numeric value from cell (may contain rank after the value)
                        # Format is typically "51.4 166" where 51.4 is the value and 166 is the rank
                        value_match = re.search(r'(\d+\.?\d*)', value_cell_text)
                        if not value_match:
                            continue
                        
                        try:
                            value = float(value_match.group(1))
                            
                            # Extract four factors based on label
                            if 'efg%' in label or 'effective' in label or 'efg' in label:
                                stats['efg_pct'] = value
                                # Also store offensive rank if available
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    stats['efg_pct_rank'] = int(rank_match.group(1))
                            elif 'to%' in label or 'turnover' in label or 'turn' in label:
                                stats['turnover_pct'] = value
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    stats['turnover_pct_rank'] = int(rank_match.group(1))
                            elif 'or%' in label or 'offensive rebound' in label or ('off' in label and 'reb' in label):
                                stats['off_reb_pct'] = value
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    stats['off_reb_pct_rank'] = int(rank_match.group(1))
                            elif 'fta/fga' in label or 'ftr' in label or 'ft rate' in label or ('ft' in label and 'rate' in label):
                                stats['fta_per_fga'] = value
                                rank_match = re.search(r'\s+(\d+)', value_cell_text)
                                if rank_match:
                                    stats['fta_per_fga_rank'] = int(rank_match.group(1))
                        except (ValueError, TypeError):
                            continue
                
                # Parse overall ratings table
                elif any(keyword in header_text for keyword in ['adj', 'rating', 'tempo', 'rank']):
                    rows = table.find_all('tr')
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 2:
                            label = cells[0].get_text(strip=True)
                            value = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                            
                            # Extract adjusted ratings
                            label_lower = label.lower()
                            if 'adjo' in label_lower or 'adj off' in label_lower or 'offensive' in label_lower:
                                try:
                                    stats['adj_offense'] = float(re.sub(r'[^\d.]', '', value))
                                except (ValueError, TypeError):
                                    pass
                            elif 'adjd' in label_lower or 'adj def' in label_lower or 'defensive' in label_lower:
                                try:
                                    stats['adj_defense'] = float(re.sub(r'[^\d.]', '', value))
                                except (ValueError, TypeError):
                                    pass
                            elif 'adjt' in label_lower or 'tempo' in label_lower:
                                try:
                                    stats['adj_tempo'] = float(re.sub(r'[^\d.]', '', value))
                                except (ValueError, TypeError):
                                    pass
                            elif 'rank' in label_lower:
                                try:
                                    rank_val = re.search(r'\d+', value)
                                    if rank_val:
                                        stats['kenpom_rank'] = int(rank_val.group())
                                except (ValueError, TypeError):
                                    pass
            
            # Method 3: Extract from text patterns (fallback)
            text = soup.get_text()
            
            # Look for patterns like "AdjO: 115.2" or "AdjO 115.2" or "AdjO. 115.2"
            patterns = [
                (r'AdjO[:\s.]+(\d+\.?\d*)', 'adj_offense'),
                (r'AdjD[:\s.]+(\d+\.?\d*)', 'adj_defense'),
                (r'AdjT[:\s.]+(\d+\.?\d*)', 'adj_tempo'),
                (r'eFG%[:\s.]+(\d+\.?\d*)', 'efg_pct'),
                (r'TO%[:\s.]+(\d+\.?\d*)', 'turnover_pct'),
                (r'OR%[:\s.]+(\d+\.?\d*)', 'off_reb_pct'),
                (r'FTR[:\s.]+(\d+\.?\d*)', 'ft_rate'),
            ]
            
            for pattern, key in patterns:
                if key not in stats:  # Only if not already found
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        try:
                            value = float(match.group(1))
                            stats[key] = value
                        except (ValueError, TypeError):
                            pass
            
            # Extract rank from various patterns
            if 'kenpom_rank' not in stats:
                rank_patterns = [
                    r'Rank[:\s]+#?(\d+)',
                    r'#(\d+)\s+rank',
                    r'ranked\s+#?(\d+)',
                ]
                for pattern in rank_patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        try:
                            stats['kenpom_rank'] = int(match.group(1))
                            break
                        except (ValueError, TypeError):
                            pass
            
            # Extract additional stats that might be on the page
            # Look for win-loss record
            wl_match = re.search(r'(\d+)-(\d+)', text)
            if wl_match:
                try:
                    stats['wins'] = int(wl_match.group(1))
                    stats['losses'] = int(wl_match.group(2))
                except (ValueError, TypeError):
                    pass
            
            # Extract conference if mentioned
            conf_match = re.search(r'(ACC|SEC|Big Ten|Big 12|Pac-12|Big East|AAC|Mountain West|WCC|A-10)', text)
            if conf_match:
                stats['conference'] = conf_match.group(1)
            
            # Log what we found
            found_stats = [k for k in stats.keys() if k not in ['team', 'source']]
            if found_stats:
                logger.info(f"Extracted {len(found_stats)} stats for {team_name}: {', '.join(found_stats)}")
                return stats
            else:
                logger.warning(f"Could not extract stats from KenPom page for {team_name}")
                # Log a sample of the HTML for debugging
                logger.debug(f"HTML sample (first 1000 chars): {html[:1000]}")
                return None
                
        except Exception as e:
            logger.error(f"Error parsing KenPom team page: {e}", exc_info=True)
            return None
    
    def get_team_rankings(self, target_date: Optional[date] = None) -> Optional[List[Dict[str, Any]]]:
        """Get all team rankings from KenPom (from cache)
        
        Args:
            target_date: Date to get rankings for (defaults to today). Cache refreshes if date doesn't match.
        """
        if target_date is None:
            target_date = date.today()
        
        # Ensure cache is for the correct date (if possible)
        if not self._is_cache_for_date(target_date) or not self._team_cache:
            if self.authenticated:
                self._refresh_homepage_cache(target_date)
        
        # If cache is empty, we can't do anything
        if not self._team_cache:
            return None
        
        # Convert cache dict to list format
        rankings = []
        for team_name, stats in self._team_cache.items():
            # Only include entries with rank (skip duplicate entries)
            if stats.get('kenpom_rank') is not None and 'team' in stats:
                rankings.append({
                    'rank': stats.get('kenpom_rank'),
                    'team': stats.get('team', team_name),
                    'adj_offense': stats.get('adj_offense'),
                    'adj_defense': stats.get('adj_defense'),
                    'adj_tempo': stats.get('adj_tempo'),
                    'net_rating': stats.get('net_rating'),
                })
        
        # Sort by rank
        rankings.sort(key=lambda x: x['rank'] if x['rank'] is not None else 9999)
        
        return rankings if rankings else None
    
    def force_refresh_cache(self, target_date: Optional[date] = None) -> bool:
        """
        Force a refresh of the KenPom homepage cache for a specific date
        
        Args:
            target_date: Date to refresh cache for (defaults to today)
        
        Returns:
            True if successful, False otherwise
        """
        if target_date is None:
            target_date = date.today()
        return self._refresh_homepage_cache(target_date)
    
    def is_authenticated(self) -> bool:
        """Check if scraper is authenticated"""
        return self.authenticated

