from setuptools import setup, find_packages

setup(
    name="vitalvida_orders",                # The name of your app/package
    version="0.1.0",                        # Initial version
    description="Webhook Intake & Queue Engine for VitalVida",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(),               # Automatically find packages inside your app folder
    include_package_data=True,              # Include non-Python files defined in MANIFEST.in or pyproject.toml
    install_requires=[
        "frappe",                           # Specify dependencies here
        # Add other required packages if your API uses them, e.g. requests
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Framework :: Frappe",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    zip_safe=False,
)
