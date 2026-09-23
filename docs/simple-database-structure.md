# Simple Database Structure Diagram

[Open the SVG](simple-database-structure.svg)

This is the simplest presentation version of the database structure.

```text
Companies
├── Users
├── Plants
│   ├── Elements
│   │   └── Data Sources
│   │       └── Readings
│   └── Monthly Costs
├── Market Prices
└── Runs
    └── Generated Documents
```

Plain-English explanation:

- **Companies** are the top-level boundary.
- **Users** belong to a company and have an access level.
- **Plants** belong to a company.
- **Elements** are plant equipment like meters, sensors, or devices.
- **Data Sources** are named measurements from equipment.
- **Readings** are timestamped values from data sources.
- **Market Prices** and **Monthly Costs** are financial data.
- **Runs** store chat/report actions.
- **Generated Documents** store report metadata for PDF, Excel, or Word files.
