"""Écran de connexion de l'agent (point 10.9).

Isolé de ``MainWindow`` : c'est une vue autonome (saisie des initiales, option
« déconnexion sur les autres postes », accès aux préférences) dont le seul lien
avec la fenêtre est le branchement des actions (``validate_login``,
``show_preferences_dialog``).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from buttons import DebounceButton

def create_login_widget(window):
    login_widget = QWidget()
    login_layout = QVBoxLayout()

    # Ajouter un label
    window.label_connexion = QLabel("Connectez-vous")
    window.label_connexion.setAlignment(Qt.AlignCenter)  # Centre le texte
    font = window.label_connexion.font()
    font.setPointSize(16)  # Augmente la taille de la police (ajustez selon vos besoins)
    font.setBold(True)  # Met le texte en gras
    window.label_connexion.setFont(font)
    login_layout.addWidget(window.label_connexion)

    # Ajouter un champ pour les initiales
    window.initials_input = QLineEdit()
    window.initials_input.setPlaceholderText("Entrez vos initiales")
    login_layout.addWidget(window.initials_input)

    # Checkbox pour la deconnexion sur tous les autres postes
    window.checkbox_on_all = QCheckBox("Déconnexion sur tous les autres postes")
    window.checkbox_on_all.setChecked(True)
    login_layout.addWidget(window.checkbox_on_all)

    # Ajouter un bouton de validation
    validate_button = DebounceButton("Valider")
    validate_button.clicked.connect(window.validate_login)
    login_layout.addWidget(validate_button)

    # Ajouter un bouton de préférences
    preferences_button = QPushButton("Préférences")
    preferences_button.clicked.connect(window.show_preferences_dialog)
    login_layout.addWidget(preferences_button)

    login_widget.setLayout(login_layout)

    # Connecter la touche Enter à la fonction de validation
    window.initials_input.returnPressed.connect(window.validate_login)

    return login_widget
