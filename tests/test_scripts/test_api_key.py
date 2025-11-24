#!/usr/bin/env python3
"""Test script to verify The Odds API key is working correctly"""

import os
import sys
import requests
from datetime import date

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

def test_api_key():
    """Test The Odds API key and verify it's working"""
    api_key = os.getenv('THE_ODDS_API_KEY')
    
    print("🔑 Testing The Odds API Key")
    print("=" * 80)
    
    if not api_key:
        print("❌ ERROR: THE_ODDS_API_KEY environment variable is not set")
        print("\nTo set it:")
        print("1. Create a .env file in the project root")
        print("2. Add: THE_ODDS_API_KEY=your_api_key_here")
        return False
    
    print(f"✅ API Key found (length: {len(api_key)} characters)")
    print(f"   First 8 chars: {api_key[:8]}...")
    print(f"   Last 8 chars: ...{api_key[-8:]}")
    
    # Test the API with a simple request
    print("\n📡 Testing API connection...")
    
    base_url = "https://api.the-odds-api.com/v4"
    target_date = date(2025, 11, 18)
    date_str = target_date.strftime('%Y-%m-%d')
    
    # First, try to get usage/quota info
    usage_url = f"{base_url}/usage"
    try:
        print(f"   Checking API usage/quota...")
        response = requests.get(usage_url, params={'apiKey': api_key}, timeout=10)
        
        if response.status_code == 200:
            usage_data = response.json()
            print(f"   ✅ Usage endpoint accessible")
            print(f"   Requests used: {usage_data.get('requests_used', 'N/A')}")
            print(f"   Requests remaining: {usage_data.get('requests_remaining', 'N/A')}")
        else:
            print(f"   ⚠️  Usage endpoint returned status {response.status_code}")
    except Exception as e:
        print(f"   ⚠️  Could not check usage: {e}")
    
    # Now test the actual odds endpoint
    print(f"\n📊 Testing odds endpoint for {target_date}...")
    sport = 'basketball_ncaab'
    url = f"{base_url}/sports/{sport}/odds"
    params = {
        'apiKey': api_key,
        'regions': 'us',
        'markets': 'spreads',
        'oddsFormat': 'american',
        'dateFormat': 'iso',
        'bookmakers': 'draftkings',
        'commenceTimeFrom': f"{date_str}T00:00:00Z",
        'commenceTimeTo': f"{date_str}T23:59:59Z"
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        
        print(f"   Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ API request successful")
            print(f"   Number of events returned: {len(data)}")
            
            if data:
                print(f"\n   Sample events:")
                for i, event in enumerate(data[:3], 1):
                    home = event.get('home_team', 'N/A')
                    away = event.get('away_team', 'N/A')
                    bookmakers = event.get('bookmakers', [])
                    print(f"   {i}. {away} @ {home} ({len(bookmakers)} bookmakers)")
            else:
                print(f"   ⚠️  No events returned (this might be normal if no games have lines)")
                
            # Check response headers for rate limit info
            if 'x-requests-remaining' in response.headers:
                remaining = response.headers['x-requests-remaining']
                print(f"\n   📈 Rate limit remaining: {remaining} requests")
            
            return True
            
        elif response.status_code == 401:
            print(f"   ❌ ERROR: Unauthorized - Invalid API key")
            print(f"   Response: {response.text[:200]}")
            return False
        elif response.status_code == 429:
            print(f"   ❌ ERROR: Rate limit exceeded")
            print(f"   Response: {response.text[:200]}")
            return False
        elif response.status_code == 400:
            print(f"   ❌ ERROR: Bad request")
            print(f"   Response: {response.text[:200]}")
            return False
        else:
            print(f"   ❌ ERROR: Unexpected status code {response.status_code}")
            print(f"   Response: {response.text[:500]}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Network error: {e}")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = test_api_key()
    sys.exit(0 if success else 1)

