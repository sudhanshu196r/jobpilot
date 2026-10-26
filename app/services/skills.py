"""Skill vocabulary and extraction.

The taxonomy maps a canonical skill name to its aliases. Aliases prefixed with
``=`` are matched case-sensitively, which is how short, ambiguous names such as
"Go" or "R" avoid matching ordinary English words. Skills a user lists that are
*not* in the taxonomy are still matched literally (see ``SkillMatcher``).
"""

from __future__ import annotations

import re
from functools import lru_cache

TAXONOMY: dict[str, list[str]] = {
    # Languages
    "Python": ["python", "python3"],
    "Java": ["java", "core java", "java 8", "java 11", "java 17"],
    "JavaScript": ["javascript", "js", "es6", "ecmascript"],
    "TypeScript": ["typescript"],
    "C": ["=C"],
    "C++": ["c++", "cpp"],
    "C#": ["c#", "csharp"],
    "Go": ["golang", "=Go"],
    "Rust": ["=Rust"],
    "Kotlin": ["kotlin"],
    "Swift": ["=Swift"],
    "Objective-C": ["objective-c", "objective c"],
    "Ruby": ["ruby"],
    "PHP": ["php"],
    "Scala": ["scala"],
    "R": ["=R"],
    "MATLAB": ["matlab"],
    "Perl": ["perl"],
    "Dart": ["=Dart"],
    "Elixir": ["elixir"],
    "Haskell": ["haskell"],
    "Bash": ["bash", "shell scripting", "shell script", "unix shell"],
    "PowerShell": ["powershell"],
    "SQL": ["sql", "t-sql", "pl/sql", "plsql"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3"],
    "Solidity": ["solidity"],
    "COBOL": ["cobol"],
    "VBA": ["vba"],
    # Frontend
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs"],
    "Angular": ["angular", "angularjs", "angular.js"],
    "Vue.js": ["vue", "vue.js", "vuejs"],
    "Nuxt": ["nuxt", "nuxt.js"],
    "Svelte": ["svelte", "sveltekit"],
    "Redux": ["redux", "redux toolkit"],
    "Tailwind CSS": ["tailwind", "tailwindcss", "tailwind css"],
    "Bootstrap": ["bootstrap"],
    "SASS": ["sass", "scss"],
    "jQuery": ["jquery"],
    "Webpack": ["webpack"],
    "Vite": ["vite"],
    "React Native": ["react native", "react-native"],
    "Flutter": ["flutter"],
    "Android": ["android", "android sdk"],
    "iOS": ["=iOS", "=IOS"],
    "SwiftUI": ["swiftui"],
    "Jetpack Compose": ["jetpack compose"],
    # Backend
    "Node.js": ["node", "node.js", "nodejs"],
    "Express": ["=Express", "express.js", "expressjs"],
    "NestJS": ["nestjs", "nest.js"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Spring Boot": ["spring boot", "springboot"],
    "Spring": ["=Spring", "spring framework", "spring mvc"],
    "Hibernate": ["hibernate", "jpa"],
    ".NET": [".net", "dotnet", ".net core", "asp.net", "asp.net core"],
    "Ruby on Rails": ["rails", "ruby on rails", "ror"],
    "Laravel": ["laravel"],
    "GraphQL": ["graphql"],
    "REST APIs": ["=REST", "restful", "rest api", "rest apis", "restful apis", "restful services"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices", "microservice", "micro-services"],
    "WebSockets": ["websocket", "websockets"],
    "OAuth": ["oauth", "oauth2", "openid connect", "oidc"],
    # Data stores
    "PostgreSQL": ["postgres", "postgresql", "psql"],
    "MySQL": ["mysql"],
    "SQL Server": ["sql server", "mssql"],
    "Oracle": ["=Oracle", "oracle db", "oracle database"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Cassandra": ["cassandra"],
    "DynamoDB": ["dynamodb"],
    "Elasticsearch": ["elasticsearch", "elastic search", "opensearch", "=ELK"],
    "SQLite": ["sqlite"],
    "Neo4j": ["neo4j"],
    "Firebase": ["firebase", "firestore"],
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery", "big query"],
    "Redshift": ["redshift"],
    "Databricks": ["databricks"],
    # Messaging / data engineering
    "Kafka": ["kafka", "apache kafka"],
    "RabbitMQ": ["rabbitmq"],
    "Spark": ["spark", "apache spark", "pyspark"],
    "Hadoop": ["hadoop", "hdfs", "hive"],
    "Airflow": ["airflow", "apache airflow"],
    "dbt": ["dbt"],
    "ETL": ["etl", "elt", "data pipelines", "data pipeline"],
    "Flink": ["flink"],
    # Cloud / DevOps
    "AWS": ["aws", "amazon web services", "ec2", "=S3", "aws lambda", "=ECS", "=EKS", "cloudformation"],
    "Azure": ["azure", "microsoft azure"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Docker": ["docker", "containers", "containerization"],
    "Kubernetes": ["kubernetes", "k8s", "helm"],
    "Terraform": ["terraform"],
    "Ansible": ["ansible"],
    "Jenkins": ["jenkins"],
    "GitHub Actions": ["github actions"],
    "GitLab CI": ["gitlab ci", "gitlab-ci"],
    "CI/CD": ["ci/cd", "ci cd", "continuous integration", "continuous delivery", "continuous deployment"],
    "Linux": ["linux", "unix", "ubuntu", "rhel", "centos"],
    "Nginx": ["nginx"],
    "Prometheus": ["prometheus"],
    "Grafana": ["grafana"],
    "Datadog": ["datadog"],
    "Git": ["git", "github", "gitlab", "bitbucket"],
    "Serverless": ["serverless"],
    "SRE": ["sre", "site reliability"],
    "Networking": ["tcp/ip", "networking", "dns", "load balancing"],
    # Data science / ML
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "neural networks", "neural network"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision", "opencv"],
    "LLMs": ["llm", "llms", "large language models", "generative ai", "genai", "gen ai", "prompt engineering"],
    "RAG": ["=RAG", "retrieval augmented generation", "retrieval-augmented generation"],
    "LangChain": ["langchain"],
    "TensorFlow": ["tensorflow", "keras"],
    "PyTorch": ["pytorch", "torch"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "Statistics": ["statistics", "statistical analysis", "hypothesis testing", "a/b testing", "ab testing"],
    "Data Analysis": ["data analysis", "data analytics", "analytics"],
    "Data Visualization": ["data visualization", "data visualisation", "dashboards", "dashboarding"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Looker": ["looker"],
    "Excel": ["excel", "ms excel", "advanced excel", "vlookup", "pivot tables"],
    "MLOps": ["mlops", "mlflow", "kubeflow"],
    # Testing
    "Unit Testing": ["unit testing", "unit tests", "tdd", "test driven development"],
    "Selenium": ["selenium", "selenium webdriver"],
    "Cypress": ["cypress"],
    "Playwright": ["playwright"],
    "Jest": ["jest"],
    "JUnit": ["junit"],
    "pytest": ["pytest"],
    "Manual Testing": ["manual testing", "test cases", "test planning"],
    "Automation Testing": ["automation testing", "test automation"],
    "API Testing": ["api testing", "postman", "rest assured"],
    "Performance Testing": ["performance testing", "load testing", "jmeter"],
    # Architecture / practices
    "System Design": ["system design", "distributed systems", "scalability", "high availability"],
    "Data Structures & Algorithms": ["data structures", "algorithms", "dsa"],
    "OOP": ["oop", "object oriented", "object-oriented", "design patterns", "solid principles"],
    "Agile": ["agile", "scrum", "kanban", "sprint planning"],
    "Jira": ["jira", "confluence"],
    "Security": ["security", "owasp", "application security", "appsec", "penetration testing", "iam"],
    "Blockchain": ["blockchain", "web3", "ethereum", "smart contracts"],
    "Embedded Systems": ["embedded", "embedded systems", "firmware", "rtos", "microcontrollers"],
    "SAP": ["=SAP", "sap hana", "sap abap", "abap", "sap fico", "sap mm", "sap sd"],
    "Salesforce": ["salesforce", "apex", "sfdc"],
    "ServiceNow": ["servicenow"],
    "Unity": ["=Unity", "unity3d"],
    "Figma": ["figma"],
    # Design / product
    "UI/UX Design": ["ui/ux", "ux", "ui design", "ux design", "user experience", "user interface", "wireframing", "prototyping"],
    "Adobe Creative Suite": ["photoshop", "illustrator", "adobe xd", "indesign", "after effects", "premiere pro"],
    "Product Management": ["product management", "product roadmap", "roadmapping", "product strategy", "prd"],
    "User Research": ["user research", "usability testing"],
    "Project Management": ["project management", "pmp", "prince2", "program management"],
    "Stakeholder Management": ["stakeholder management", "stakeholder communication"],
    "Business Analysis": ["business analysis", "business analyst", "requirement gathering", "requirements gathering", "brd"],
    # Business functions
    "Digital Marketing": ["digital marketing", "online marketing", "performance marketing"],
    "SEO": ["seo", "search engine optimization"],
    "SEM": ["=SEM", "google ads", "ppc", "adwords"],
    "Social Media Marketing": ["social media marketing", "smm", "social media"],
    "Content Writing": ["content writing", "copywriting", "content creation", "technical writing"],
    "Email Marketing": ["email marketing", "mailchimp", "marketing automation", "hubspot"],
    "Google Analytics": ["google analytics", "ga4"],
    "Sales": ["sales", "b2b sales", "inside sales", "business development", "lead generation"],
    "CRM": ["crm", "zoho crm"],
    "Account Management": ["account management", "key account management", "client relationship"],
    "Customer Support": ["customer support", "customer service", "technical support", "helpdesk"],
    "Accounting": ["accounting", "bookkeeping", "tally", "gst", "tds", "accounts payable", "accounts receivable"],
    "Financial Analysis": ["financial analysis", "financial modelling", "financial modeling", "fp&a", "budgeting", "forecasting"],
    "Auditing": ["audit", "auditing", "internal audit"],
    "Taxation": ["taxation", "income tax", "direct tax", "indirect tax"],
    "Recruitment": ["recruitment", "talent acquisition", "candidate sourcing"],
    "HR Operations": ["hr operations", "payroll", "employee onboarding", "hrms", "employee engagement"],
    "Supply Chain": ["supply chain", "logistics", "procurement", "inventory management", "vendor management"],
    "Operations": ["operations management", "process improvement", "lean manufacturing", "six sigma"],
    "Communication": ["communication skills", "verbal communication", "written communication"],
    "Leadership": ["leadership", "team leadership", "people management", "mentoring", "team management"],
}


def _alias_pattern(alias: str) -> re.Pattern[str]:
    case_sensitive = alias.startswith("=")
    body = re.escape(alias.lstrip("="))
    body = body.replace(r"\ ", r"[\s\-]+")
    # Boundaries treat + # . as word characters so "C++" != "C" and ".NET" stands alone.
    pattern = rf"(?<![A-Za-z0-9+#])(?<![A-Za-z0-9]\.){body}(?![A-Za-z0-9+#])(?!\.[A-Za-z0-9])"
    if alias == "=Go":
        pattern += r"(?![\s\-]+(?:to|ahead|live|through|beyond)\b)"
    return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, list[re.Pattern[str]]]]:
    out = []
    for canonical, aliases in TAXONOMY.items():
        names = {canonical.lower(), *[a for a in aliases]}
        patterns = []
        for a in sorted(names, key=len, reverse=True):
            if a.startswith("="):
                patterns.append(_alias_pattern(a))
            elif not any(x.startswith("=") and x[1:].lower() == a for x in aliases):
                patterns.append(_alias_pattern(a))
        out.append((canonical, patterns))
    return out


_LOOKUP: dict[str, str] = {}
for _canon, _aliases in TAXONOMY.items():
    _LOOKUP[_canon.lower()] = _canon
    for _a in _aliases:
        _LOOKUP.setdefault(_a.lstrip("=").lower(), _canon)


def canonical(skill: str) -> str:
    """Canonical spelling of a skill, or the input tidied up if unknown."""
    s = " ".join(skill.split())
    return _LOOKUP.get(s.lower(), s)


def extract(text: str) -> list[str]:
    """Taxonomy skills mentioned in ``text``, in first-appearance order."""
    if not text:
        return []
    found: list[tuple[int, str]] = []
    for canon, patterns in _compiled():
        best = None
        for p in patterns:
            m = p.search(text)
            if m and (best is None or m.start() < best):
                best = m.start()
        if best is not None:
            found.append((best, canon))
    found.sort()
    return [c for _, c in found]


class SkillMatcher:
    """Matches a fixed set of user skills (taxonomy or free-form) against text."""

    def __init__(self, skills: list[str]):
        self.skills = []
        self._patterns: dict[str, list[re.Pattern[str]]] = {}
        compiled = dict(_compiled())
        for s in skills:
            canon = canonical(s)
            if canon in self._patterns:
                continue
            self.skills.append(canon)
            self._patterns[canon] = compiled.get(canon) or [_alias_pattern(canon)]

    def present(self, text: str) -> list[str]:
        return [s for s in self.skills if any(p.search(text) for p in self._patterns[s])]

    def contains(self, text: str, skill: str) -> bool:
        pats = self._patterns.get(canonical(skill))
        return bool(pats and any(p.search(text) for p in pats))
