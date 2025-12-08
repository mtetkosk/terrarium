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
                    logger.info(f"Loaded KenPom cache with {len(self._team_cache)} teams (cached on {self._cache_date})")
            except Exception as e:
                logger.warning(f"Failed to load KenPom cache: {e}")
                self._team_cache = {}
                self._cache_date = None
    
    def _save_cache(self, target_date: Optional[date] = None) -> None:
        """Save team data to cache file for a specific date
        
        Args:
            target_date: Date to save cache for (defaults to today)
        """
        if target_date is None:
            target_date = date.today()
        
        try:
            # Only store one day's data at a time - overwrite previous cache
            cache_data = {
                'cache_date': target_date.isoformat(),
                'teams': self._team_cache
            }
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            self._cache_date = target_date
            logger.info(f"Saved KenPom cache with {len(self._team_cache)} teams for {target_date}")
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
                # Defensive Rating (DRtg)
                # KenPom has two possible table structures:
                # 1. Simple structure (test/simplified): DRtg column (first) = AdjD value
                # 2. Complex structure (real KenPom): 
                #    - Column 6: DRtg (first) = rank (not the AdjD value)
                #    - Column 11: DRtg (second) = change/delta value (NOT the AdjD value!)
                #    - Column 15: Actual AdjD value (no header - it's in an unlabeled column)
                elif header_lower == 'drtg':
                    drtg_occurrence_count += 1
                    if drtg_occurrence_count == 1:
                        # First occurrence - could be AdjD value (simple structure) or rank (complex structure)
                        # We'll check the value later to determine which it is
                        col_indices['_drtg_first_col'] = i  # Store for later processing
                    elif drtg_occurrence_count == 2:
                        # Second occurrence - this is the CHANGE value in complex structure
                        # Store this index so we can find the AdjD value column after it
                        col_indices['_drtg_change_col'] = i  # Store change column for reference
                        # Mark that we have complex structure
                        col_indices['_has_complex_structure'] = True
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
                # Luck
                # In complex structure: Luck value is in the second DRtg column (column 11), not the Luck header column (column 8)
                # The Luck header column contains a rank, not the actual Luck value
                elif header_lower == 'luck':
                    # Store this for simple structure, but in complex structure we'll use the second DRtg column
                    if not col_indices.get('_has_complex_structure', False):
                        col_indices['luck'] = i
                    # In complex structure, we'll extract Luck from the second DRtg column (change column)
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
                    
                    # AdjD parsing - handle both simple and complex table structures
                    adjd_value = None
                    has_complex = col_indices.get('_has_complex_structure', False)
                    change_col = col_indices.get('_drtg_change_col')
                    drtg_first_col = col_indices.get('_drtg_first_col')
                    
                    if has_complex and change_col is not None:
                        # Complex structure: Scan columns after the change column
                        for offset in [3, 4, 5, 6]:
                            scan_idx = change_col + offset
                            if scan_idx < len(cells):
                                test_value = safe_float(cells[scan_idx].get_text(strip=True))
                                if test_value is not None and 70 <= test_value <= 130:
                                    adjd_value = test_value
                                    logger.debug(f"Found AdjD value {adjd_value} for {team_name} at column {scan_idx} (complex structure)")
                                    break
                    elif drtg_first_col is not None:
                        # Simple structure: First DRtg column contains the AdjD value
                        if drtg_first_col < len(cells):
                            test_value = safe_float(cells[drtg_first_col].get_text(strip=True))
                            if test_value is not None and 70 <= test_value <= 130:
                                adjd_value = test_value
                                logger.debug(f"Found AdjD value {adjd_value} for {team_name} at column {drtg_first_col} (simple structure)")
                    
                    # Validate and store AdjD value
                    if adjd_value is not None and 70 <= adjd_value <= 130:
                        team_stats['adj_defense'] = adjd_value
                        # Don't create duplicate 'drtg' field - only use 'adj_defense'
                    elif drtg_first_col is not None or change_col is not None:
                        logger.warning(
                            f"Could not find AdjD value for {team_name} in expected range (70-130). "
                            f"First DRtg col: {drtg_first_col}, Change col: {change_col}"
                        )
                    
                    if 'adj_tempo' in col_indices:
                        idx = col_indices['adj_tempo']
                        if idx < len(cells):
                            # AdjT is at the second NetRtg column (column 9), not the AdjT header column (column 7)
                            team_stats['adj_tempo'] = safe_float(cells[idx].get_text(strip=True))
                            # Don't create duplicate 'adjt' field - only use 'adj_tempo'
                    
                    # Luck parsing - handle both simple and complex structures
                    has_complex = col_indices.get('_has_complex_structure', False)
                    change_col = col_indices.get('_drtg_change_col')
                    
                    if has_complex and change_col is not None:
                        # Complex structure: Luck value is in the second DRtg column (change column)
                        if change_col < len(cells):
                            luck_value = safe_float(cells[change_col].get_text(strip=True))
                            if luck_value is not None:
                                team_stats['luck'] = luck_value
                                logger.debug(f"Found Luck value {luck_value} for {team_name} at change column {change_col} (complex structure)")
                    elif 'luck' in col_indices:
                        # Simple structure: Luck is in the Luck column
                        idx = col_indices['luck']
                        if idx < len(cells):
                            team_stats['luck'] = safe_float(cells[idx].get_text(strip=True))
                    
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
        
        if not self.authenticated:
            logger.warning("Not authenticated with KenPom, cannot fetch team stats")
            return None
        
        # Check if cache is for the correct date - refresh if not matching
        if not self._is_cache_for_date(target_date) or not self._team_cache:
            if not self._is_cache_for_date(target_date):
                logger.info(f"KenPom cache is for {self._cache_date}, but need {target_date}. Refreshing...")
            else:
                logger.info("KenPom cache is empty, refreshing from homepage...")
            
            if not self._refresh_homepage_cache(target_date):
                logger.warning("Failed to refresh KenPom cache")
                if not self._team_cache:
                    return None
        
        # CRITICAL: Normalize input team name FIRST before any matching
        # This ensures matching happens on normalized names on BOTH sides
        # Step 1: Normalize (removes mascots, standardizes format)
        normalized = normalize_team_name_for_lookup(team_name)
        # Step 2: Map to canonical form
        canonical_name = map_team_name_to_canonical(team_name)
        
        logger.debug(f"Looking up team '{team_name}' -> normalized: '{normalized}', canonical: '{canonical_name}'")
        
        # Step 3: Try direct lookup with canonical name (primary lookup)
        # Cache keys are already normalized from cache building, so this matches normalized to normalized
        if canonical_name in self._team_cache:
            stats = self._team_cache[canonical_name].copy()
            logger.debug(f"Found KenPom stats for '{team_name}' (normalized: '{normalized}') via canonical name: '{canonical_name}'")
            return stats
        
        # Step 4: Try normalized name (in case it's stored as alias)
        if normalized in self._team_cache:
            stats = self._team_cache[normalized].copy()
            logger.debug(f"Found KenPom stats for '{team_name}' (normalized: '{normalized}') via normalized name")
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
                return stats
            if lookup_normalized in self._team_cache:
                stats = self._team_cache[lookup_normalized].copy()
                logger.debug(f"Found KenPom stats for '{team_name}' (normalized: '{normalized}') via variation normalized: '{lookup_normalized}'")
                return stats
        
        # If still not found, use LLM for fuzzy matching
        # Log normalized name so it's clear what we're trying to match
        logger.info(f"No direct or partial match found for '{team_name}' (normalized: '{normalized}'), trying LLM fuzzy matching...")
        matched_team = self._llm_fuzzy_match_team(team_name)
        if matched_team:
            # Normalize and map the LLM-matched team name
            matched_normalized = normalize_team_name_for_lookup(matched_team)
            matched_canonical = map_team_name_to_canonical(matched_team)
            
            # Try canonical name first
            if matched_canonical in self._team_cache:
                logger.debug(f"Found KenPom stats for {team_name} via LLM fuzzy match (canonical): {matched_canonical}")
                return self._team_cache[matched_canonical].copy()
            # Try normalized name
            if matched_normalized in self._team_cache:
                logger.debug(f"Found KenPom stats for {team_name} via LLM fuzzy match (normalized): {matched_normalized}")
                return self._team_cache[matched_normalized].copy()
        
        logger.warning(f"Could not find KenPom stats for '{team_name}' (normalized: '{normalized}') in cache (tried direct, partial, and LLM matching)")
        return None
    
    def _llm_fuzzy_match_team(self, team_name: str) -> Optional[str]:
        """
        Use LLM to fuzzy match a team name to a KenPom team name
        
        Args:
            team_name: Team name to match (e.g., "Rice Owls", "Tennessee Volunteers")
            
        Returns:
            Matched KenPom team name or None if no match found
        """
        try:
            from src.utils.llm import get_llm_client
            
            # Get list of available team names from cache (limit to top 100 for efficiency)
            available_teams = []
            for cached_name, stats in self._team_cache.items():
                # Only include entries that look like team names (have rank, not normalized keys)
                if stats.get('kenpom_rank') is not None and stats.get('team'):
                    # Use the canonical team name from stats
                    canonical_name = stats.get('team', cached_name)
                    if canonical_name and canonical_name not in available_teams:
                        available_teams.append(canonical_name)
                        if len(available_teams) >= 100:  # Limit to avoid token limits
                            break
            
            if not available_teams:
                logger.warning("No teams available in cache for LLM matching")
                return None
            
            # Sort by rank to prioritize better teams
            available_teams.sort(key=lambda t: next(
                (s.get('kenpom_rank', 9999) for s in self._team_cache.values() if s.get('team') == t),
                9999
            ))
            
            llm_client = get_llm_client("Researcher")  # Use Researcher's model (usually cheapest)
            
            system_prompt = """You are a helper that matches team names to KenPom team names.

Given a team name and a list of available KenPom team names, find the best match.
Team names may vary (e.g., "Rice Owls" might match "Rice", "Tennessee Volunteers" might match "Tennessee").

Return ONLY the exact team name from the available list, or "null" if no good match exists.
Be lenient with matching - consider:
- Nicknames vs official names (e.g., "Volunteers" -> "Tennessee")
- Full names vs short names (e.g., "Rice Owls" -> "Rice")
- Common abbreviations (e.g., "NC State" -> "N.C. State")

CRITICAL: Distinguish between similar team names:
- "Miami Red", "Miami Red Hawks", "Miami Redhawks", "Miami Ohio", "Miami (Oh)", "miami oh" -> Match to "Miami OH" or "Miami (OH)" (MAC conference)
- "Miami FL", "Miami (FL)", "Miami Florida", "Miami Hurricanes" -> Match to "Miami FL" or "Miami (FL)" (ACC conference)
These are DIFFERENT teams - do NOT mix them up!

Return JSON: {"matched_team": "exact name from list" or null}"""

            user_prompt = f"""Team to match: "{team_name}"

Available KenPom teams (first 100):
{json.dumps(available_teams[:100], indent=2)}

Find the best match for "{team_name}". Return only the exact name from the list, or null if no good match."""
            
            try:
                response = llm_client.call(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,  # Low temperature for deterministic matching
                    max_tokens=50,
                    parse_json=True
                )
                
                matched_team = response.get('matched_team')
                if matched_team and matched_team != "null":
                    logger.info(f"LLM matched '{team_name}' to '{matched_team}'")
                    return matched_team
                else:
                    logger.debug(f"LLM found no match for '{team_name}'")
                    return None
                    
            except Exception as e:
                logger.warning(f"LLM fuzzy matching failed for {team_name}: {e}")
                return None
                
        except ImportError:
            logger.warning("LLM client not available for fuzzy matching")
            return None
        except Exception as e:
            logger.warning(f"Error in LLM fuzzy matching for {team_name}: {e}")
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
                if any(keyword in header_text for keyword in ['efg%', 'to%', 'or%', 'ftr', 'four factors']):
                    rows = table.find_all('tr')[1:]  # Skip header
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 2:
                            label = cells[0].get_text(strip=True).lower()
                            value = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                            
                            # Extract four factors
                            if 'efg%' in label or 'effective' in label:
                                try:
                                    stats['efg_pct'] = float(re.sub(r'[^\d.]', '', value))
                                except (ValueError, TypeError):
                                    pass
                            elif 'to%' in label or 'turnover' in label:
                                try:
                                    stats['turnover_pct'] = float(re.sub(r'[^\d.]', '', value))
                                except (ValueError, TypeError):
                                    pass
                            elif 'or%' in label or 'offensive rebound' in label:
                                try:
                                    stats['off_reb_pct'] = float(re.sub(r'[^\d.]', '', value))
                                except (ValueError, TypeError):
                                    pass
                            elif 'ftr' in label or 'ft rate' in label:
                                try:
                                    stats['ft_rate'] = float(re.sub(r'[^\d.]', '', value))
                                except (ValueError, TypeError):
                                    pass
                
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
        
        if not self.authenticated:
            logger.warning("Not authenticated with KenPom, cannot fetch rankings")
            return None
        
        # Ensure cache is for the correct date
        if not self._is_cache_for_date(target_date) or not self._team_cache:
            if not self._refresh_homepage_cache(target_date):
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

