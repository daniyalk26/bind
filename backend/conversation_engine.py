
#backend/conversation_engine.py

import re
import json
from typing import Tuple, Dict, Any, Optional
from datetime import datetime

from backend.schemas import ConversationState
import backend.models as models

class ConversationEngine:
    def __init__(self):
        self.state_prompts = {
            ConversationState.start:                "Hello! I'll help you with your insurance onboarding. Let's start by getting your zip code.",
            ConversationState.collecting_zip:       "What's your 5-digit zip code?",
            ConversationState.collecting_name:      "Great! What's your full name?",
            ConversationState.collecting_email:     "Thanks! What's your email address?",
            ConversationState.collecting_vehicle_info:  "Perfect! Please provide either your VIN or Year Make Body-Type (e.g. '2022 Honda Civic').",
            ConversationState.collecting_vehicle_use:   "How do you primarily use this vehicle? (commuting, commercial, farming, or business)",
            ConversationState.collecting_blind_spot:    "Does this vehicle have blind spot warning? (Yes or No)",
            ConversationState.collecting_commute_days:  "How many days per week do you commute with this vehicle?",
            ConversationState.collecting_commute_miles: "What's your one-way distance to work/school in miles?",
            ConversationState.collecting_annual_mileage:"What's the estimated annual mileage for this vehicle?",
            ConversationState.ask_more_vehicles:        "Would you like to add another vehicle? (Yes or No)",
            ConversationState.collecting_license_type:  "What type of license do you have? (Foreign, Personal, or Commercial)",
            ConversationState.collecting_license_status:"What's your license status? (Valid or Suspended)",
            ConversationState.completed:               "Thank you! Your onboarding is complete."
        }

        self.validators = {
            ConversationState.collecting_zip:            self._validate_zip,
            ConversationState.collecting_name:           self._validate_name,
            ConversationState.collecting_email:          self._validate_email,
            ConversationState.collecting_vehicle_info:   self._validate_vehicle_info,
            ConversationState.collecting_vehicle_use:    self._validate_vehicle_use,
            ConversationState.collecting_blind_spot:     self._validate_yes_no,
            ConversationState.collecting_commute_days:   self._validate_days,
            ConversationState.collecting_commute_miles:  self._validate_miles,
            ConversationState.collecting_annual_mileage: self._validate_mileage,
            ConversationState.ask_more_vehicles:         self._validate_yes_no,
            ConversationState.collecting_license_type:   self._validate_license_type,
            ConversationState.collecting_license_status: self._validate_license_status,
        }

    def get_prompt(self, state: ConversationState) -> str:
        return self.state_prompts.get(state, "")

    def validate_input(self, state: ConversationState, user_input: str) -> Tuple[bool, Any, Optional[str]]:
        validator = self.validators.get(state)
        if validator:
            return validator(user_input)
        return True, user_input, None

    def get_next_state(self, current_state: ConversationState, validated_data: Any, state_data: Dict) -> ConversationState:
        transitions = {
            ConversationState.start:                   ConversationState.collecting_zip,
            ConversationState.collecting_zip:          ConversationState.collecting_name,
            ConversationState.collecting_name:         ConversationState.collecting_email,
            # jump directly to validation
            ConversationState.collecting_email:        ConversationState.collecting_vehicle_info,
            ConversationState.collecting_vehicle_info: ConversationState.collecting_vehicle_use,
            ConversationState.collecting_vehicle_use:  ConversationState.collecting_blind_spot,
            ConversationState.collecting_blind_spot:   self._next_after_blind_spot,
            ConversationState.collecting_commute_days: ConversationState.collecting_commute_miles,
            ConversationState.collecting_commute_miles:ConversationState.ask_more_vehicles,
            ConversationState.collecting_annual_mileage:ConversationState.ask_more_vehicles,
            ConversationState.ask_more_vehicles:         self._next_after_more_vehicles,
            ConversationState.collecting_license_type:   self._next_after_license_type,
            ConversationState.collecting_license_status: ConversationState.completed,
            ConversationState.completed:                 ConversationState.completed,
        }
        next_state = transitions[current_state]
        return next_state(validated_data, state_data) if callable(next_state) else next_state

    def calculate_progress(self, state: ConversationState) -> float:
        progress_map = {
            ConversationState.start:                    0,
            ConversationState.collecting_zip:           10,
            ConversationState.collecting_name:          20,
            ConversationState.collecting_email:         30,
            ConversationState.vehicle_intro:            35,
            ConversationState.collecting_vehicle_info:  40,
            ConversationState.collecting_vehicle_use:   50,
            ConversationState.collecting_blind_spot:    60,
            ConversationState.collecting_commute_days:  65,
            ConversationState.collecting_commute_miles: 70,
            ConversationState.collecting_annual_mileage:70,
            ConversationState.ask_more_vehicles:        75,
            ConversationState.collecting_license_type:  85,
            ConversationState.collecting_license_status:95,
            ConversationState.completed:               100,
        }
        return progress_map[state]

    # ─── Transition Helpers ────────────────────────────────────────────── #
    def _next_after_blind_spot(self, _: Any, state_data: Dict) -> ConversationState:
        # branch based on exactly "commuting"
        return (ConversationState.collecting_commute_days
                if state_data.get("current_vehicle", {}).get("vehicle_use") == models.VehicleUse.commuting.value
                else ConversationState.collecting_annual_mileage)

    def _next_after_more_vehicles(self, add_more: bool, __: Dict) -> ConversationState:
        return (ConversationState.vehicle_intro if add_more else ConversationState.collecting_license_type)

    def _next_after_license_type(self, lic: str, __: Dict) -> ConversationState:
        return (ConversationState.completed if lic == models.LicenseType.foreign.value
                else ConversationState.collecting_license_status)

    # ─── Validators ───────────────────────────────────────────────────── #
    def _validate_zip(self, text: str) -> Tuple[bool, str, Optional[str]]:
        txt = text.strip()
        if re.fullmatch(r"\d{5}", txt):
            return True, txt, None
        return False, "", "Please provide a valid 5-digit zip code."

    def _validate_name(self, text: str) -> Tuple[bool, str, Optional[str]]:
        txt = text.strip()
        if len(txt.split()) >= 2:
            return True, txt, None
        return False, "", "Please provide your full name (first and last)."

    def _validate_email(self, text: str) -> Tuple[bool, str, Optional[str]]:
        txt = text.strip().lower()
        if re.fullmatch(r"[^@]+@[^@]+\.[^@]+", txt):
            return True, txt, None
        return False, "", "Please provide a valid email address."

    def _validate_vehicle_info(self, text: str) -> Tuple[bool, Dict[str,Any], Optional[str]]:
        vin = text.strip().upper()
        if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
            return True, {"vin": vin}, None

        parts = text.strip().split(maxsplit=2)
        if len(parts) == 3:
            try:
                year = int(parts[0])
                if 1900 <= year <= datetime.utcnow().year + 1:
                    return True, {
                        "year": year,
                        "make": parts[1].title(),
                        "body_type": parts[2].title(),
                    }, None
            except:
                pass
        return False, {}, "Please provide either a 17-character VIN or 'Year Make Body-Type'."

    def _validate_vehicle_use(self, text: str) -> Tuple[bool, str, Optional[str]]:
        txt = text.strip().lower()
        if txt in (v.value for v in models.VehicleUse):
            return True, txt, None
        return False, "", "Please choose: commuting, commercial, farming, or business."

    def _validate_yes_no(self, text: str) -> Tuple[bool, bool, Optional[str]]:
        txt = text.strip().lower()
        if txt in {"yes","y","yeah","sure","ok","okay"}:
            return True, True, None
        if txt in {"no","n","nope","nah"}:
            return True, False, None
        return False, False, "Please answer Yes or No."

    def _validate_days(self, text: str) -> Tuple[bool, int, Optional[str]]:
        try:
            n = int(text.strip())
            if 1 <= n <= 7:
                return True, n, None
        except:
            pass
        return False, 0, "Please enter a number between 1 and 7."

    def _validate_miles(self, text: str) -> Tuple[bool, int, Optional[str]]:
        try:
            m = int(text.strip())
            if 1 <= m < 1000:
                return True, m, None
        except:
            pass
        return False, 0, "Please enter the number of miles (1-999)."

    def _validate_mileage(self, text: str) -> Tuple[bool, int, Optional[str]]:
        try:
            m = int(text.strip().replace(",",""))
            if 1 <= m < 500_000:
                return True, m, None
        except:
            pass
        return False, 0, "Please enter annual mileage (e.g., 12000)."

    def _validate_license_type(self, text: str) -> Tuple[bool, str, Optional[str]]:
        txt = text.strip().lower()
        if txt in (t.value for t in models.LicenseType):
            return True, txt, None
        return False, "", "Please choose: Foreign, Personal, or Commercial."

    def _validate_license_status(self, text: str) -> Tuple[bool, str, Optional[str]]:
        txt = text.strip().lower()
        if txt in (s.value for s in models.LicenseStatus):
            return True, txt, None
        return False, "", "Please choose: Valid or Suspended."
