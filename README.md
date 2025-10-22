# 💸 Personal Finance Dashboard

A full-stack **personal finance dashboard** for tracking investments, holdings, and real-time portfolio data — built with **FastAPI**, **Streamlit**, **MongoDB**, and **Docker**.

It includes secure authentication, live financial data integration, and a responsive dashboard UI.

---

## 🚀 Features

- **User authentication** (Register, Login)
- **Portfolio tracking** — add, update, and delete holdings
- **Automatic market price updates** via Yahoo Finance
- **Realized gains calculation**
- **Tax estimation** (placeholder endpoint)
- **Dockerized deployment**
- **Continuous Integration** using GitHub Actions
- **Automated tests** for API routes (FastAPI + Pytest)

---

## 🧰 Tech Stack

| Layer | Technology |
|-------|-------------|
| Backend | [FastAPI](https://fastapi.tiangolo.com/) |
| Frontend | [Streamlit](https://streamlit.io/) |
| Database | [MongoDB](https://www.mongodb.com/) |
| Containerization | [Docker](https://www.docker.com/) |
| Testing | [Pytest](https://docs.pytest.org/) |
| CI/CD | [GitHub Actions](https://github.com/features/actions) |

---

## 🧩 Folder Structure
personal-finance-dashboard/
│
├── backend/
│   ├── main.py                # FastAPI app
│   ├── requirements.txt       # Backend dependencies
│   ├── test_app.py            # Pytest suite
│   ├── Dockerfile             # Backend container
│
├── frontend/
│   ├── app.py                 # Streamlit dashboard
│   ├── requirements.txt       # Frontend dependencies
│   ├── Dockerfile             # Frontend container
│
├── .github/workflows/
│   └── ci.yml                 # CI/CD pipeline
│
└── README.md
---

## ⚙️ Local Setup

### 1️⃣ Clone the repository

```bash
git clone https://github.com/<your-username>/personal-finance-dashboard.git
cd personal-finance-dashboard
```

### 2️⃣ Setup environment variables

create a .env file in the backend/ directory:
```bash 
MONGO_URI=mongodb://localhost:27017
TWELVE_API_KEY=your_api_key_here
```

### 3️⃣ Run the backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```
Access the API docs at 👉 http://localhost:8000/docs

### 4️⃣ Run the frontend
```bash
cd ../frontend
pip install -r requirements.txt
streamlit run app.py
```
Your dashboard will open automatically at http://localhost:8501
---

## Docker Deployment

Build and run both services together:
```bash
docker-compose up --build
```
Or manually build and run each container:
```bash
docker build -t finance_backend ./backend
docker build -t finance_frontend ./frontend
docker run -p 8000:8000 finance_backend
docker run -p 8501:8501 finance_frontend
```
---

## Testing
Run backend test locally:
```bash
cd backend
pytest -v
```
GitHub Actions will automatically:
	•	Lint code with flake8
	•	Format with black
	•	Run Pytest on each push
	•	Build and push Docker images
	•	Deploy to Docker Hub
---

## Author

David Glover
🎓 University of California, Santa Cruz
📫 Contact: [david05glover@gmail.com]
🌐 github.com/dvdthr5

## License
This project is licensed under the MIT License.
---
