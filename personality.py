import os
import logging
from groq import Groq

logging.basicConfig(level=logging.INFO)

# Abby's Identity
ABBY_IDENTITY = """You are Abby, a warm and trustworthy financial assistant for Nigerian churches.
You speak like an educated church member who genuinely cares about financial health.
Never cold, never robotic. Use natural language, acknowledge people first, and celebrate milestones."""

def get_first_name(sender_phone, full_name=None):
    if full_name:
        return full_name.split()[0].capitalize()
    return "Friend"

def extract_intent_natural(message):
    """When message doesn't match a command, use Groq to find natural intent."""
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": """You are Abby, a warm and intelligent financial assistant for Nigerian churches.
The user has sent a natural message. Determine their intent from this list:
- greeting: hello, hi, good morning
- question_balance: want to know balance or net
- question_expenses: asking about expenses
- question_income: asking about income
- question_specific: asking about a specific transaction or person
- confusion: lost, don't know what to do
- complaint: frustrated or reporting a problem
- gratitude: saying thank you
- other: none of the above

Return ONLY JSON: {"intent": "intent_name", "extracted_query": "any useful detail"}"""},
                {"role": "user", "content": message}
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=256
        )
        import json
        raw = completion.choices[0].message.content.strip()
        if raw.startswith("```json"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logging.error(f"Intent extraction error: {e}")
        return {"intent": "other", "extracted_query": ""}

def craft_response(intent, data, user_context):
    """Main response generator. All WhatsApp replies pass through here."""
    name = get_first_name(user_context.get("sender_phone"), user_context.get("full_name"))
    
    if intent == "greeting":
        return f"Hey {name}! 😊 Good to hear from you. How can I help today?\n\n_Abby • ChurchBooks AI by Intravent_"
        
    elif intent == "question_balance":
        net = data.get("net", "unknown")
        income = data.get("income", "unknown")
        expenses = data.get("expenses", "unknown")
        period = data.get("period", "this month")
        return (
            f"You're currently sitting at ₦{net} net for {period}, {name}.\n"
            f"Income came in at ₦{income} and expenses were ₦{expenses}.\n"
            f"Want the full breakdown? Just say _SUMMARY {period}_\n\n"
            f"_Abby • ChurchBooks AI by Intravent_"
        )
        
    elif intent == "confusion":
        return (
            f"No worries at all, {name} 🙏 I'm here to help.\n\n"
            f"You can send me a voice note to log finances, or type HELP\n"
            f"to see everything I can do. What would you like to do?\n\n"
            f"_Abby • ChurchBooks AI by Intravent_"
        )
        
    elif intent == "gratitude":
        return (
            f"Always a pleasure, {name} 😊\n"
            f"The church's finances are in good hands. Have a blessed day!\n\n"
            f"_Abby • ChurchBooks AI by Intravent_"
        )
        
    elif intent == "complaint":
        return (
            f"So sorry to hear that, {name}. Let me help fix this.\n"
            f"Can you tell me a bit more about what happened?\n"
            f"I want to make sure everything is sorted properly.\n\n"
            f"_Abby • ChurchBooks AI by Intravent_"
        )
        
    elif intent == "question_specific":
        query = data.get("extracted_query", "")
        return (
            f"Let me look into that for you, {name}.\n"
            f"Regarding '{query}' — I'll pull up the details now.\n\n"
            f"_Abby • ChurchBooks AI by Intravent_"
        )
        
    elif intent == "other":
        return (
            f"Thanks for reaching out, {name} 😊\n"
            f"I'm right here if you need anything.\n\n"
            f"_Abby • ChurchBooks AI by Intravent_"
        )
    
    return f"Thanks, {name}! I'm on it.\n\n_Abby • ChurchBooks AI by Intravent_"

def craft_confirmation(entries, name, net_amount):
    """Human-like confirmation instead of robotic 'Recorded: ...'"""
    lines = [f"Got it, {name} 👍 Let me read that back to you:\n"]
    for e in entries:
        label = e['category'].capitalize()
        amt = f"₦{int(e['amount']):,}"
        programme = e.get('programme', '')
        programme_tag = f" — {programme}" if programme else ""
        lines.append(f"- {label}{programme_tag} — {amt} ({e['type']})")
    
    lines.append(f"\nThat's a net of ₦{net_amount:,} this round. Looks right?")
    lines.append("Reply *YES* to save it or *NO* if something's off.")
    return "\n".join(lines)

def craft_smart_confirmation(entries, name, net_amount, overall_confidence, low_confidence_reason="", transcript_issues=None):
    """Intelligent confirmation based on confidence level."""
    high_entries = [e for e in entries if e.get('extraction_confidence') == 'high']
    medium_entries = [e for e in entries if e.get('extraction_confidence') == 'medium']
    low_entries = [e for e in entries if e.get('extraction_confidence') == 'low']
    
    if overall_confidence == "high":
        # Standard warm confirmation
        lines = [f"Got it, {name} 👍 Here's what I caught:\n"]
        for e in entries:
            label = e['category'].capitalize()
            amt = f"₦{int(e['amount']):,}"
            programme = e.get('programme', '')
            programme_tag = f" — {programme}" if programme else ""
            lines.append(f"- {label}{programme_tag} — {amt} ✅")
            # Show raw text for medium confidence entries
            if e.get('extraction_confidence') == 'medium' and e.get('raw_text_used'):
                lines.append(f"  _(from: '{e['raw_text_used']}')_")
        
        income_total = sum(int(e['amount']) for e in entries if e['type'] == 'income')
        expense_total = sum(int(e['amount']) for e in entries if e['type'] == 'expense')
        
        if income_total > 0 and expense_total > 0:
            net_display = f"₦{net_amount:,}" if net_amount >= 0 else f"-₦{abs(net_amount):,}"
            lines.append(f"\nNet: ₦{income_total:,} income, ₦{expense_total:,} expenses ({net_display})")
        elif income_total > 0:
            lines.append(f"\nTotal income: ₦{income_total:,}")
        elif expense_total > 0:
            lines.append(f"\nTotal expenses: ₦{expense_total:,}")
        
        lines.append("Looks right? Reply *YES* to save or *NO* if anything's off.")
        return "\n".join(lines)
    
    elif overall_confidence == "medium":
        # Flag uncertain entries
        lines = [f"I got most of that, {name} — just want to double-check:\n"]
        
        for e in high_entries:
            label = e['category'].capitalize()
            amt = f"₦{int(e['amount']):,}"
            programme = e.get('programme', '')
            programme_tag = f" — {programme}" if programme else ""
            lines.append(f"✅ {label}{programme_tag} — {amt} (clear)")
        
        for e in medium_entries:
            label = e['category'].capitalize()
            amt = f"₦{int(e['amount']):,}"
            programme = e.get('programme', '')
            programme_tag = f" — {programme}" if programme else ""
            lines.append(f"❓ {label}{programme_tag} — {amt}")
            if e.get('raw_text_used'):
                lines.append(f"  _(I heard '{e['raw_text_used']}' — is that right?)_")
        
        for e in low_entries:
            label = e['category'].capitalize()
            amt = f"₦{int(e['amount']):,}"
            programme = e.get('programme', '')
            programme_tag = f" — {programme}" if programme else ""
            lines.append(f"⚠️ {label}{programme_tag} — {amt} (unclear)")
        
        lines.append(f"\nReply *YES* if it's correct, *NO* to cancel, or tell me what to fix.")
        return "\n".join(lines)
    
    else:
        # Low confidence — don't guess
        snippet = ""
        if low_entries and low_entries[0].get('raw_text_used'):
            snippet = low_entries[0]['raw_text_used']
        elif medium_entries and medium_entries[0].get('raw_text_used'):
            snippet = medium_entries[0]['raw_text_used']
        
        if not snippet:
            snippet = low_confidence_reason if low_confidence_reason else "parts of the audio were unclear"
        
        lines = [
            f"I caught some of that but I'm not confident enough to save it as-is, {name} 🙏",
            f"",
            f"What I heard: '{snippet}'",
            f"",
            f"Could you try again? A few tips:",
            f"- Speak the amounts clearly: 'one hundred and fifty thousand naira'",
            f"- Pause briefly between each item",
            f"- If you're in a noisy place, move somewhere quieter",
            f"",
            f"I want to make sure every figure is exactly right 💛"
        ]
        return "\n".join(lines)

def craft_error(name):
    return (
        f"Hmm, I didn't quite catch the figures in that one, {name} 🙏\n"
        f"Could you try again? Speak the amounts clearly —\n"
        f"for example: 'Offering was one hundred and fifty thousand naira.'\n"
        f"I'll get it right the second time!"
    )

def craft_help(name, role="treasurer"):
    return (
        f"Hey {name}! Here's everything I can do for you 😊\n\n"
        f"💬 *Logging finances:* Just send me a voice note anytime —\n"
        f"   morning, after service, whenever is convenient.\n\n"
        f"📊 *Getting reports:\n"
        f"   • _SHOW EXPENSES this week_ — see what went out\n"
        f"   • _SHOW INCOME january_ — income for any month\n"
        f"   • _SUMMARY this month_ — full picture\n\n"
        f"🔍 *Checking on things:\n"
        f"   • _SHOW COLLECTOR [name]_ — what someone submitted\n"
        f"   • _SHOW FLAGS_ — entries that need attention\n\n"
        f"✏️ *Fixing a mistake:\n"
        f"   • _CORRECT [id] [reason]_ — flag an entry for pastor review\n\n"
        f"Just talk to me naturally and I'll figure out what you need 🙏\n\n"
        f"_Abby • ChurchBooks AI by Intravent_"
    )

def craft_onboarding_welcome():
    """First message for new users."""
    return (
        "Welcome to ChurchBooks! 🙏✨\n\n"
        "I'm Abby, your AI-powered financial assistant. "
        "I'm here to help your church keep clean, accurate records — "
        "no stress, no spreadsheets, just simple conversation.\n\n"
        "To get started, what should I call you? 😊"
    )

def craft_onboarding_name_saved(name):
    """After user provides their name."""
    return (
        f"Nice to meet you, {name}! 😊\n\n"
        f"What's your role in the church? "
        f"(e.g., Treasurer, Pastor, Admin, Usher)\n\n"
        f"This helps me tailor how I present information to you."
    )

def craft_onboarding_complete(name, role):
    """After user provides their role — onboarding done."""
    return (
        f"You're all set, {name}! 🎉\n\n"
        f"Role saved as *{role}*.\n\n"
        f"Here's how we'll work together:\n\n"
        f"💬 *To record:* Just send a voice note or type what happened\n"
        f"   e.g., _Sunday offering was 200k, we spent 30k on fuel_\n\n"
        f"📊 *To check:* Ask me anything — balance, expenses, who gave what\n\n"
        f"Type *HELP* anytime to see everything I can do.\n\n"
        f"Let's keep those books clean! 📖✨\n\n"
        f"_Abby • ChurchBooks AI by Intravent_"
    )

def append_insight(base_message, insight_text):
    """Append a single insight to a confirmation message naturally."""
    if insight_text:
        return f"{base_message}\n\n{insight_text}"
    return base_message