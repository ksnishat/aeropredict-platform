"""
AeroPredict Flask Admin Panel
Admin interface for experiment management and model version browsing
"""

import os
import mlflow
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_admin import Admin
from flask_admin.contrib.mlflow import ModelView
from flask_admin.base import BaseView, expose
import requests

# Configuration
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "aeropredict-admin-secret-change-in-production")
API_URL = os.getenv("API_URL", "http://localhost:8000")

# Initialize Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY

# Initialize MLflow
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

# Initialize Flask-Admin
admin = Admin(
    app,
    name="AeroPredict Admin",
    template_mode="bootstrap4",
    index_view=None  # We'll create custom index
)


class MLflowExperimentView(BaseView):
    """Custom view for MLflow experiments"""

    @expose("/")
    def index(self):
        try:
            client = mlflow.tracking.MlflowClient()
            experiments = client.search_experiments()
            return self.render("admin/experiments.html", experiments=experiments)
        except Exception as e:
            flash(f"Error loading experiments: {e}", "error")
            return self.render("admin/experiments.html", experiments=[])

    @expose("/<experiment_id>")
    def experiment_detail(self, experiment_id):
        try:
            client = mlflow.tracking.MlflowClient()
            experiment = client.get_experiment(experiment_id)
            runs = client.search_runs(
                experiment_ids=[experiment_id],
                order_by=["start_time DESC"],
                max_results=100
            )
            return self.render("admin/experiment_detail.html", experiment=experiment, runs=runs)
        except Exception as e:
            flash(f"Error loading experiment: {e}", "error")
            return redirect(url_for("mlflow_experiment.index"))


class MLflowModelRegistryView(BaseView):
    """Custom view for MLflow Model Registry"""

    @expose("/")
    def index(self):
        try:
            client = mlflow.tracking.MlflowClient()
            models = client.search_registered_models()
            return self.render("admin/models.html", models=models)
        except Exception as e:
            flash(f"Error loading models: {e}", "error")
            return self.render("admin/models.html", models=[])

    @expose("/<model_name>")
    def model_detail(self, model_name):
        try:
            client = mlflow.tracking.MlflowClient()
            model = client.get_registered_model(model_name)
            versions = client.get_latest_versions(model_name, stages=["None", "Staging", "Production", "Archived"])
            return self.render("admin/model_detail.html", model=model, versions=versions)
        except Exception as e:
            flash(f"Error loading model: {e}", "error")
            return redirect(url_for("mlflow_model_registry.index"))

    @expose("/<model_name>/transition", methods=["POST"])
    def transition_model(self, model_name):
        version = request.form.get("version")
        stage = request.form.get("stage")
        try:
            client = mlflow.tracking.MlflowClient()
            client.transition_model_version_stage(
                name=model_name,
                version=version,
                stage=stage,
                archive_existing_versions=False
            )
            flash(f"Model {model_name} v{version} transitioned to {stage}", "success")
        except Exception as e:
            flash(f"Error transitioning model: {e}", "error")
        return redirect(url_for("mlflow_model_registry.model_detail", model_name=model_name))


class SystemHealthView(BaseView):
    """System health monitoring view"""

    @expose("/")
    def index(self):
        health_data = {}

        # Check MLflow
        try:
            response = requests.get(f"{MLFLOW_TRACKING_URI}/health", timeout=5)
            health_data["mlflow"] = {"status": "healthy" if response.status_code == 200 else "unhealthy", "url": MLFLOW_TRACKING_URI}
        except Exception:
            health_data["mlflow"] = {"status": "unreachable", "url": MLFLOW_TRACKING_URI}

        # Check API
        try:
            response = requests.get(f"{API_URL}/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                health_data["api"] = {"status": "healthy", "model_loaded": data.get("model_loaded", False), "url": API_URL}
            else:
                health_data["api"] = {"status": "unhealthy", "url": API_URL}
        except Exception:
            health_data["api"] = {"status": "unreachable", "url": API_URL}

        # Check Airflow (if accessible)
        airflow_url = os.getenv("AIRFLOW_URL", "http://localhost:8080")
        try:
            response = requests.get(f"{airflow_url}/health", timeout=5)
            health_data["airflow"] = {"status": "healthy" if response.status_code == 200 else "unhealthy", "url": airflow_url}
        except Exception:
            health_data["airflow"] = {"status": "unreachable", "url": airflow_url}

        return self.render("admin/health.html", health_data=health_data)


class DashboardView(BaseView):
    """Custom dashboard view"""

    @expose("/")
    def index(self):
        # Get summary statistics
        try:
            client = mlflow.tracking.MlflowClient()
            experiments = client.search_experiments()
            models = client.search_registered_models()

            stats = {
                "total_experiments": len(experiments),
                "total_models": len(models),
                "production_models": sum(1 for m in models for v in client.get_latest_versions(m.name, stages=["Production"])),
                "staging_models": sum(1 for m in models for v in client.get_latest_versions(m.name, stages=["Staging"])),
            }
        except Exception:
            stats = {
                "total_experiments": 0,
                "total_models": 0,
                "production_models": 0,
                "staging_models": 0,
            }

        return self.render("admin/dashboard.html", stats=stats)


# Register admin views
admin.add_view(DashboardView(name="Dashboard", endpoint="dashboard", category="Overview"))
admin.add_view(MLflowExperimentView(name="Experiments", endpoint="mlflow_experiment", category="MLflow"))
admin.add_view(MLflowModelRegistryView(name="Model Registry", endpoint="mlflow_model_registry", category="MLflow"))
admin.add_view(SystemHealthView(name="System Health", endpoint="system_health", category="Monitoring"))

# API routes for AJAX calls
@app.route("/api/experiments/<experiment_id>/runs")
def api_experiment_runs(experiment_id):
    """Get runs for an experiment via AJAX"""
    try:
        client = mlflow.tracking.MlflowClient()
        runs = client.search_runs(
            experiment_ids=[experiment_id],
            order_by=["start_time DESC"],
            max_results=50
        )
        return jsonify([{
            "run_id": run.info.run_id,
            "status": run.info.status,
            "start_time": run.info.start_time,
            "metrics": run.data.metrics,
            "params": run.data.params
        } for run in runs])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/models/<model_name>/versions")
def api_model_versions(model_name):
    """Get model versions via AJAX"""
    try:
        client = mlflow.tracking.MlflowClient()
        versions = client.get_latest_versions(model_name, stages=["None", "Staging", "Production", "Archived"])
        return jsonify([{
            "version": v.version,
            "stage": v.current_stage,
            "creation_time": v.creation_timestamp,
            "last_updated": v.last_updated_timestamp,
            "run_id": v.run_id,
            "description": v.description
        } for v in versions])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def api_health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "aeropredict-admin"})


# Error handlers
@app.errorhandler(404)
def not_found(e):
    return render_template("admin/404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("admin/500.html"), 500


if __name__ == "__main__":
    port = int(os.getenv("FLASK_ADMIN_PORT", "8081"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)