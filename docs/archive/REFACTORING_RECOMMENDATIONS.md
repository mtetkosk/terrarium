# Refactoring Recommendations

## Overview
After reviewing the codebase, I've identified the top 2 refactoring opportunities that will significantly reduce code complexity and improve maintainability.

---

## 🔧 Refactor #1: Extract Data Conversion Layer

### Problem
The `Coordinator` class contains ~400 lines of data conversion logic across 4 methods:
- `_convert_picks_from_json()` (~80 lines)
- `_convert_sized_picks_from_json()` (~100 lines)  
- `_convert_compliance_results_from_json()` (~80 lines)
- `_convert_card_review_from_json()` (~120 lines)

These methods share significant duplication:
- Repeated game_id parsing (string/int conversion, team name matching)
- Repeated bet_type parsing and validation
- Repeated line extraction from selection strings using regex
- Repeated odds parsing (string to int conversion)
- Repeated pick matching logic using (game_id, bet_type, line) tuples

### Solution
Create a centralized `DataConverter` class in `src/orchestration/data_converter.py`:

```python
class DataConverter:
    """Centralized data conversion between JSON, objects, and database models"""
    
    @staticmethod
    def parse_game_id(game_id_str: str, games: List[Game]) -> Optional[int]:
        """Parse game_id from string, handling various formats"""
    
    @staticmethod
    def parse_bet_type(bet_type_str: str) -> BetType:
        """Parse and validate bet type"""
    
    @staticmethod
    def parse_odds(odds_str: str) -> int:
        """Parse odds from string format"""
    
    @staticmethod
    def extract_line_from_selection(selection: str) -> float:
        """Extract line value from selection string"""
    
    @staticmethod
    def picks_from_json(candidate_picks: List[Dict], games: List[Game]) -> List[Pick]:
        """Convert JSON picks to Pick objects"""
    
    @staticmethod
    def picks_to_dict(picks: List[Pick]) -> List[Dict]:
        """Convert Pick objects to dict format for agents"""
    
    @staticmethod
    def compliance_results_from_json(...) -> List[ComplianceResult]:
        """Convert compliance JSON to ComplianceResult objects"""
    
    @staticmethod
    def card_review_from_json(...) -> CardReview:
        """Convert President JSON to CardReview object"""
```

### Benefits
- **Reduces coordinator.py by ~300-400 lines** (from 1124 to ~800 lines)
- **Eliminates duplication** - shared parsing logic in one place
- **Improves testability** - conversion logic can be unit tested independently
- **Easier to maintain** - changes to conversion logic happen in one place
- **Reusable** - other parts of codebase can use the same converters

### Implementation Steps
1. Create `src/orchestration/data_converter.py`
2. Move all conversion methods from `Coordinator` to `DataConverter`
3. Extract common parsing logic into static helper methods
4. Update `Coordinator` to use `DataConverter` instance
5. Add unit tests for conversion logic

---

## 🔧 Refactor #2: Extract Workflow Steps & Optimize ReportGenerator

### Problem
1. **Massive workflow method**: `run_daily_workflow()` is 400+ lines doing everything
2. **Repeated instantiation**: `ReportGenerator` is created 9 times (lines 133, 149, 166, 197, 301, 361, 399, 446, 461) when it should be created once
3. **Hard to test**: Individual workflow steps can't be tested in isolation
4. **Hard to understand**: The workflow logic is buried in one massive method

### Solution
Break down `run_daily_workflow()` into focused step methods:

```python
class Coordinator:
    def __init__(self, db: Optional[Database] = None):
        # ... existing init ...
        self.report_generator = ReportGenerator(self.db)  # Create once
    
    def run_daily_workflow(...) -> CardReview:
        """Orchestrate workflow steps"""
        # High-level orchestration only
        self._step_process_results(target_date, force_refresh)
        games = self._step_scrape_games(target_date, test_limit)
        lines = self._step_scrape_lines(games)
        
        # Main workflow loop
        for revision_count in range(max_revisions + 1):
            insights = self._step_research(games, target_date, lines, force_refresh)
            predictions = self._step_model(insights, lines, target_date)
            picks = self._step_pick(predictions, insights, lines, target_date)
            sized_picks = self._step_bank(picks, target_date)
            compliance_results = self._step_compliance(sized_picks, insights, target_date)
            review = self._step_president(picks, compliance_results, insights, predictions, target_date)
            
            if not review.revision_requests or revision_count >= max_revisions:
                break
        
        self._step_finalize(review, picks, target_date)
        return review
    
    def _step_research(self, games, target_date, lines, force_refresh) -> Dict:
        """Step 3: Researcher researches games"""
        # Move research logic here (~20 lines)
    
    def _step_model(self, insights, lines, target_date) -> Dict:
        """Step 4: Modeler generates predictions"""
        # Move modeling logic here (~20 lines)
    
    def _step_pick(self, predictions, insights, lines, target_date) -> List[Pick]:
        """Step 5: Picker selects picks"""
        # Move picking logic here (~30 lines)
    
    # ... etc for each step
```

### Benefits
- **Reduces complexity** - each step method is 20-40 lines vs 400+ line monolith
- **Improves readability** - workflow is clear from method names
- **Enables testing** - each step can be tested independently
- **Fixes ReportGenerator** - instantiated once, reused throughout
- **Easier to modify** - changes to one step don't affect others

### Implementation Steps
1. Create `self.report_generator` in `__init__`
2. Extract each workflow step into its own method
3. Update `run_daily_workflow()` to call step methods
4. Replace all `ReportGenerator(self.db)` with `self.report_generator`
5. Add docstrings explaining each step

---

## Impact Summary

### Lines of Code Reduction
- **Refactor #1**: ~300-400 lines removed from coordinator.py
- **Refactor #2**: ~100-150 lines improved (better organization, not necessarily fewer)
- **Total**: Coordinator goes from 1124 lines to ~600-700 lines (40% reduction)

### Code Quality Improvements
- ✅ Eliminates duplication in conversion logic
- ✅ Improves testability (conversion logic and workflow steps can be tested separately)
- ✅ Better separation of concerns
- ✅ Easier to maintain and extend
- ✅ Fixes inefficient object creation (ReportGenerator)

### Estimated Effort
- **Refactor #1**: 2-3 hours (extract and test conversion logic)
- **Refactor #2**: 1-2 hours (extract workflow steps, fix ReportGenerator)
- **Total**: 3-5 hours for both refactors

---

## Next Steps
1. Start with Refactor #1 (Data Conversion Layer) - highest impact
2. Then Refactor #2 (Workflow Steps) - improves maintainability
3. Add unit tests for new converter class
4. Update any documentation that references the old structure

