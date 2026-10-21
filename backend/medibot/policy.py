import re

COLLECTION_ROLES = {
    'general': ('doctor', 'nurse', 'billing_executive', 'technician', 'admin'),
    'clinical': ('doctor', 'admin'),
    'nursing': ('doctor', 'nurse', 'admin'),
    'billing': ('billing_executive', 'admin'),
    'equipment': ('technician', 'admin'),
}
ROLES = ('doctor', 'nurse', 'billing_executive', 'technician', 'admin')
SQL_ROLES = {'billing_executive', 'admin'}


def allowed_collections(role: str) -> list[str]:
    return [name for name, roles in COLLECTION_ROLES.items() if role in roles]


def infer_intent(question: str) -> tuple[str, set[str]]:
    """Routing hints only. Authorization remains independent of intent detection."""
    patterns = {
        'billing': r'\b(billing|insurance|insurer|claims?|reimbursements?|ICD(?:-10)?|CPT)\b',
        'equipment': r'\b(equipment|calibrat\w*|maintenance|fault codes?|service manuals?)\b',
        'clinical': r'\b(drug formulary|diagnostic (?:reference|guidelines?|protocols?)|treatment protocols?|clinical protocols?)\b',
        'nursing': r'\b(nursing|infection control|ICU procedures?)\b',
    }
    targets = {name for name, pattern in patterns.items() if re.search(pattern, question, re.I)}
    # Questions about rules, deadlines and instructions are answered by the
    # document corpus even when they mention a number and the word "claim".
    document_rule = re.search(
        r'\b(polic(?:y|ies)|procedures?|guidelines?|instructions?|requirements?|manuals?|'
        r'codes?|documents? (?:needed|required)|submission (?:deadline|window)|'
        r'deadline|time limit|how (?:do|should|can) (?:I|we)|'
        r'how many (?:days|hours|weeks|months).{0,60}(?:have to|allowed|must|should|submit|file)|'
        r'how (?:long|soon).{0,60}(?:submit|file)|steps? (?:to|for))\b', question, re.I,
    )
    operation = re.search(r'\b(how many|count|total|average|sum|statistics|percentage|rate|most|least|highest|lowest|breakdown|number of|list|show|find|retrieve|by (?:status|department|category|month))\b', question, re.I)
    database_subject = re.search(r'\b(claims?|tickets?|claimed amount|approved amount)\b', question, re.I)
    # A patient-specific billing request is a record lookup even without words
    # such as "show" or "count". Policies and instructions still use documents.
    patient_reference = re.search(r'\b(?:PAT[-_ ]?\d+|patient[_ -]?(?:id|identifier|number))\b', question, re.I)
    billing_record = re.search(r'\b(?:billing|bills?|claims?|invoices?|payments?|claimed amount|approved amount)\b', question, re.I)
    patient_billing_lookup = patient_reference and billing_record
    use_database = (operation and database_subject or patient_billing_lookup) and not document_rule
    return ('sql_rag' if use_database else 'hybrid_rag'), targets


def refusal(role: str, restricted: set[str] | None = None, sql: bool = False) -> str:
    label = role.replace('_', ' ')
    access = ', '.join(allowed_collections(role))
    if sql:
        return f'As a {label}, you do not have access to SQL analytics. Analytics are available to billing executives and admins. You can ask about the {access} collections.'
    denied = ', '.join(sorted(restricted or set()))
    return f'As a {label}, you do not have access to {denied} documents. You can ask about the {access} collections.'
