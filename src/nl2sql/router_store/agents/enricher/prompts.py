ENRICHMENT_PROMPT = """You are a domain-aware AI assistant helping to build a search index for a manufacturing database.
Generate 5 diverse semantic variations of the following user question to retrieve relevant documents.

Rules:
1. Use domain-specific terminology (e.g., "production" -> "fabrication", "staff" -> "operators").
2. Keep variations concise.
3. Do not change the core intent (e.g., if asking for "count", don't change to "list").
4. Return ONLY the variations as a newline-separated list.

Examples:
Question: "List all machines"
Variations:
Show all equipment
List active machinery
Enumerate manufacturing assets
View production units
List CNC and manual machines

Question: "Who is on shift?"
Variations:
List active operators
Show current staff schedule
View personnel on duty
Who is working right now?
List employees clocked in

Question: "{question}"
Variations:"""
