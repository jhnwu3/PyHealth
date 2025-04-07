from datetime import datetime, timedelta
from typing import Any, Dict, List

import polars as pl

from .base_task import BaseTask

LABITEMS = [
    ### Electrolytes & Metabolic
    # Sodium
    "50824",
    "52455",
    "50983",
    "52623",
    # Potassium
    "50822",
    "52452",
    "50971",
    "52610",
    # Chloride
    "50806",
    "52434",
    "50902",
    "52535",
    # Calculated Bicarbonate
    "50803",
    "50804",
    # Glucose
    "50809",
    "52027",
    "50931",
    "52569",
    # Free Calcium
    "50808",
    "51624",
    # Magnesium
    "50960",
    # Anion Gap
    "50868",
    "52500",
    # Osmolality
    "52031",
    "50964",
    "51701",
    # Phosphate
    "50970",

    # TODO: add more lab items
    ### Renal
    # Creatinine, Whole Blood
    # Estimated GFR (MDRD equation)
    # Urea Nitrogen (BUN)

    ### Liver
    # Albumin, Blood
    # Total Bilirubin
    # Direct Bilirubin
    # Alanine Aminotransferase (ALT)
    # Asparate Aminotransferase (AST)
    # Alkaline Phosphatase

    ### Blood gases
    # pH
    # pCO2
    # pO2
    # Base Excess
    # Oxygen Saturation
    # Lactate

    ### Hematology
    # Hemoglobin
    # Hematocrit
    # White Blood Cells
    # Platelet Count

    ### Coagulation
    # D-Dimer
    # INR (PT)
    # PTT
    # Fibrinogen

    ### Cardiac
    # Troponin I
    # NTproBNP

    ### Inflammation
    # C-Reactive Protein

    ### Ventilation support
    # Ventilation Rate
    # O2 Flow
    # PEEP
    # Intubated
    # Ventilator
]


class InHospitalMortalityMIMIC4(BaseTask):
    """Task for predicting in-hospital mortality using MIMIC-IV dataset.

    Attributes:
        task_name (str): The name of the task.
        input_schema (Dict[str, str]): The input schema for the task.
        output_schema (Dict[str, str]): The output schema for the task.
    """
    task_name: str = "InHospitalMortalityMIMIC4"
    input_schema: Dict[str, str] = {"labs": "timeseries"}
    output_schema: Dict[str, str] = {"mortality": "binary"}

    def __call__(self, patient: Any) -> List[Dict[str, Any]]:
        """Processes a single patient for the in-hospital mortality prediction task.

        Args:
            patient (Any): A Patient object containing patient data.

        Returns:
            List[Dict[str, Any]]: A list of samples, each sample is a dict with patient_id,
            admission_id, labs, and mortality as keys.
        """
        input_window_hours = 48
        samples = []

        demographics = patient.get_events(event_type="patients")
        assert len(demographics) == 1
        demographics = demographics[0]

        anchor_age = int(demographics.anchor_age)        
        if anchor_age < 18:
            return []
    
        admissions = patient.get_events(event_type="admissions")

        for admission in admissions:
            admission_dischtime = datetime.strptime(admission.dischtime, "%Y-%m-%d %H:%M:%S")
            duration_hour = (admission_dischtime - admission.timestamp).total_seconds() / 3600
            if duration_hour <= input_window_hours:
                continue
            predict_time = admission.timestamp + timedelta(hours=input_window_hours)

            labevents_df = patient.get_events(
                event_type="labevents",
                start=admission.timestamp,
                end=predict_time,
                return_df=True
            )
            labevents_df = labevents_df.filter(
                pl.col("labevents/itemid").is_in(LABITEMS)
            )
            labevents_df = labevents_df.with_columns(
                pl.col("labevents/storetime").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S")
            )
            labevents_df = labevents_df.filter(
                (pl.col("labevents/storetime") <= predict_time)
            )
            if labevents_df.height == 0:
                continue

            labevents_df = labevents_df.select(
                pl.col("timestamp"),
                pl.col("labevents/itemid"),
                pl.col("labevents/valuenum").cast(pl.Float64)
            )
            labevents_df = labevents_df.pivot(
                index="timestamp",
                columns="labevents/itemid",
                values="labevents/valuenum"
            )
            labevents_df = labevents_df.sort("timestamp")

            # add missing columns with NaN values
            existing_cols = set(labevents_df.columns) - {"timestamp"}
            missing_cols = [str(item) for item in LABITEMS if str(item) not in existing_cols]
            for col in missing_cols:
                labevents_df = labevents_df.with_columns(pl.lit(None).alias(col))
        
            # reorder columns by LABITEMS
            labevents_df = labevents_df.select(
                "timestamp",
                *[str(item) for item in LABITEMS]
            )
            
            timestamps = labevents_df["timestamp"].to_list()
            lab_values = labevents_df.drop("timestamp").to_numpy()

            mortality = int(admission.hospital_expire_flag)

            samples.append(
                {
                    "patient_id": patient.patient_id,
                    "admission_id": admission.hadm_id,
                    "labs": (timestamps, lab_values),
                    "mortality": mortality,
                }
            )

        return samples
