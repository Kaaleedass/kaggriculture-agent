# 🎮 Kaggriculture Gameplay Agent

A Python-based autonomous gameplay agent designed to maximize in-game cash in a farming simulation through strategic decision-making, resource management, and task allocation.

## 🚀 Overview

The Kaggriculture agent makes decisions based on the current game state, including available money, crops, livestock, seeds, workers, land, and market prices.

The objective is to **maximize final cash by the end of a 30-day game simulation**.

The agent evaluates the game state and selects actions for the farmer, hired workers, and market transactions.

## ✨ Key Features

- 🌱 **Crop Management** — Selects crops based on growth cycles, seed availability, and remaining game time.
- 🐄 **Livestock Strategy** — Manages animal purchases, feeding, product harvesting, and fertilizer collection.
- 💰 **Economic Optimization** — Balances spending, selling, cash reserves, and investments.
- 👨‍🌾 **Worker Coordination** — Assigns tasks to the farmer and hired workers.
- 🗺️ **Land Expansion** — Evaluates opportunities to unlock additional farm areas.
- 🧠 **State-Based Decision-Making** — Uses current game observations to choose actions.
- ⏳ **Endgame Planning** — Considers the remaining turns when deciding which crops to plant and when to liquidate inventory.

## 🛠️ Technologies

- Python
- Rule-based autonomous decision-making
- Resource allocation and optimization strategies
- Simulation-based gameplay

## 🏗️ Agent Architecture

The agent follows a structured decision-making process:

1. **Observe:** Read the current game state, farm tiles, inventory, money, and market prices.
2. **Analyze:** Identify crop, livestock, worker, and market opportunities.
3. **Plan:** Prioritize actions based on resources, workload, and the remaining game horizon.
4. **Execute:** Return actions for the farmer, hired workers, and market.
5. **Adapt:** Recalculate decisions using the next game observation.

## 🌾 Gameplay Strategy

### Crop Management
- Prioritizes higher-value crops when sufficient growing time remains.
- Waters crops when watering can contribute value.
- Harvests mature crops and plants available seeds.
- Assigns workers to compact lanes to coordinate field operations.

### Livestock Management
- Purchases animals when cash reserves and farm capacity permit.
- Prioritizes animal placement near the shed to reduce travel.
- Allocates workers to feeding, harvesting animal products, collecting fertilizer, and animal care.
- Maintains wheat reserves to support feeding.

### Financial Management
- Sells harvested products and fertilizer according to market conditions.
- Maintains cash buffers before making investments.
- Evaluates worker hiring and land expansion.
- Liquidates remaining inventory near the end of the simulation.

## 📂 Project Structure

```text
kaggriculture-game-agent/
│
├── best10.py       # Main gameplay agent
└── README.md       # Project documentation
```

## ▶️ Integration

The main entry point is:

```python
def agent(obs):
    return _agent_impl(obs)
```

The agent receives the current game observation and returns a dictionary containing:

- `farmer` — the farmer's action
- `hands` — actions assigned to hired workers
- `market` — market transactions and purchases

The script is designed to integrate with the Kaggriculture game environment that supplies the expected observation structure and supports the actions used by the agent.

**Note:** The game environment and its dependencies are not included in this repository unless added separately.

## 🎯 Objective

Maximize final in-game cash by balancing:

- Crop revenue
- Livestock and fertilizer production
- Worker costs
- Seed and feed purchases
- Land investments
- Remaining inventory and time

## 🔮 Future Improvements

- Benchmark the agent across multiple game runs.
- Compare alternative crop and livestock strategies.
- Tune strategy parameters using simulation results.
- Add performance metrics and decision logs.
- Explore reinforcement learning or other learning-based strategies.

## 👨‍💻 Author

Developed as a Python-based autonomous gameplay and decision-optimization project.

## 📜 License

Add a license if you intend to permit others to reuse or modify this project.
